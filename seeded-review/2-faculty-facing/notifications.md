# Notifications - issues, comments and mail the system sends

Prose the system writes into GitHub issues and comments, plus the mail that rides beside
some of them, plus the short strings it writes as repo, team and label descriptions.
Faculty read all of these. Edit the text; I'll port it back to the generator named under
each.

Placeholders in `{braces}` are filled at runtime. Demo values elsewhere:
course org `hertie-dsl-demo-course-e1234`, cohort org `hertie-dsl-demo-f2026`.

---

## 1. Site-sync overwrite notice
`dsl_course/site_repo.py:858-940` · opened in the cohort's site repo (PUBLIC) when a sync
replaces a hand-edited generated file. Deduped by exact title; commented on if already open.
Every failure here is swallowed - a notice must never red the sync.

**Title:** `Manual edits to generated site files are overwritten by the sync`

```
The site sync regenerates parts of this repo from the org structure, so an edit made directly here is replaced the next time it runs. It has just replaced:

- `{path}` - edited by @{login} in [`{sha:0:7}`](https://github.com/{org}/{site}/commit/{sha})

Nothing is lost - each link above is the commit that was overwritten, so the change can be copied back out of it.

Make the edit at the source instead, and it survives every sync:

- **Staff cards** - the cohort's `classroom-config/people.yml` (for a public course site, the `people:` block of the course org's `.github/dsl-course.yml`).
- **Schedule rows, sessions, assignments** - the org structure and the cohort's `classroom-config/schedule.yml`.

The sync owns `_lectures/`, `_assignments/`, `_events/`, `_data/people.yml` and a few `_config.yml` keys, and names the source in a header where the file format allows one. Everything else in this repo is yours and is never rewritten.
```

One `- ` line per overwritten path. When a commit author's git email maps to no GitHub
account the row says `` `{git author name}` `` instead of `@{login}`, and this is appended
once:
```
cc @{org}/instructors - a commit author's email is not linked to a GitHub account, so they could not be mentioned directly.
```

---

## 2. Unattended cron failure, and the mail beside it
`dsl_course/workflows_render.py:321-412` · appended to every cron-bearing workflow
(**Sync membership**, **Sync site**, **Scheduled release**, **Refresh actions**,
**Publish course website**). Filed in the repo the workflow runs in - for the course-org
buttons that is the PUBLIC `.github`. Deduped by title; closes itself on the next green
run. Skipped for a `workflow_dispatch` failure (someone is watching that run); a manual
SUCCESS still closes the issue. Two channels fire together, throttled off the same
`report` output: the issue (below) and a mail to the toolkit MAINTAINER
(`dsl_course.notify.notify_run_failed`, `dsl_course/notify.py:525-540`) - the only
person who can fix a broken run, and the one person GitHub's own scheduled-failure email
never reaches.

**Title:** `{workflow name} is failing`

**First report** (the issue body):
```
The unattended run failed or was cancelled: {RUN_URL}

Nothing retries it before the next scheduled run. This issue closes itself once a run succeeds.

The toolkit maintainer has been emailed the log - nothing for teaching staff to do.

cc @{org}/course-admin
```

**Repeat report** - a comment on the open issue, and only once the thread has been quiet
for six hours, without the cc (whoever it reached the first time is already subscribed)
and without the maintainer line repeated as a *fresh* notice (it is simply part of the
same standing text):
```
The unattended run failed or was cancelled: {RUN_URL}

Nothing retries it before the next scheduled run. This issue closes itself once a run succeeds.

The toolkit maintainer has been emailed the log - nothing for teaching staff to do.
```

**Closing comment:** `Recovered: {RUN_URL}`

**The maintainer's mail** - the failed step's own log, teed to a file by the step itself and
tailed to 30 lines / 4KB:

**Subject:** `[{course_org}] {workflow} is failing`
```
{RUN_URL}

Last lines of the failed step:

{tail of the failed step's own output}
```
Example, tail from a broken **Scheduled release** run:
```
Subject: [hertie-dsl-demo-course-e1234] Scheduled release is failing

https://github.com/hertie-dsl-demo-course-e1234/.github/actions/runs/123456789

Last lines of the failed step:

Traceback (most recent call last):
  File "scheduler.py", line 214, in main
    sync_all(course_org)
RuntimeError: could not resolve central toolkit ref 'release'
```
Never sent when mail is not configured (`mailer.maintainer_address()` is None) - the issue
above is then the only channel. Never fails a second time: this runs in the already-red
job's own failure tail, so it always exits 0.

---

## 3. Schedule validation failure
`templates/classroom-config/validate-schedule.yml:178-227` · filed in the cohort's
`classroom-config` on a push that breaks `schedule.yml`, assigned to whoever pushed
(unassigned if that fails). Commented on if already open.

**Title:** `schedule.yml has entries the scheduler cannot read`

```
Validation of `schedule.yml` failed.

​```
{validator report - see section 5}
​```

Commit: {SHA}
Run: {RUN_URL}

A dropped entry is silently absent from the term plan - no release, or no deadline, snapshot or autograding. Fix `schedule.yml` on `main` and this issue closes itself.

Field reference: https://github.com/{central}/blob/{central_ref}/docs/07-schedule-releases.md
```

**Closing comment:** `schedule.yml now parses with nothing dropped.`

**Job-log annotations** from the same run (Actions renders these against `schedule.yml` in
the commit's own diff), one `::warning::` per source fault, now carrying the line number
where the parser found one:
```
::error file=schedule.yml::schedule.yml has entries the scheduler cannot read - see the run summary
::warning file=schedule.yml,line={N}::{one line per source fault - see section 5}
```

**A third failure mode**, with no channel of its own: an empty course org means the
toolkit could not read this cohort's own `dsl-course.yml` - nothing faculty can fix. It
reds the run and asks for the maintainer by name, because this repo (a cohort org) cannot
email them:
```
::error::could not read this cohort's dsl-course.yml - the source check could not run. This is not a fault in your file - the toolkit could not reach the course org. Tell the maintainer.
```

---

## 4. Schedule.yml commit comment
`dsl_course/schedule.py:1586-1621` (`source_comment`) · left on the pushed commit itself
by the same `validate-schedule.yml` run, only when the push leaves something inside the
digest's notify window (`sources_notify == 'true'`). The push is the cheapest moment to
say it: the person who wrote the line is still at their keyboard. Distant faults get
nothing - the digest issue (section 6) holds those.

```
This push leaves {n} planned release(s) inside 24h whose materials are not in the course org:
- {entry} -> {field} - [`schedule.yml:{N}`]({deep link}) - {what} - fires {when}

{n} planned release(s) have already fired with nothing to ship:
- {entry} -> {field} - [`schedule.yml:{N}`]({deep link}) - {what} - fires {when}

You will get one email about each as its deadline nears.
```

The workflow appends one more line the engine does not write, naming the digest issue that
holds the full record:
```
The generated GitHub record issue is {url}.
```

Worked example (two faults inside 24h, one already fired):
```
This push leaves 2 planned release(s) inside 24h whose materials are not in the course org:
- releases.lecture-6 -> course_source_path - [`schedule.yml:142`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L142) - `course-materials-f2026/lectures/06_week-6` does not exist yet - fires Sun 04 Oct 2026, 14:00 Europe/Berlin
- assignments.assignment-4-project -> course_source_repo - [`schedule.yml:210`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L210) - assignment-4-project-f2026 does not exist in hertie-dsl-demo-course-e1234 - fires Mon 05 Oct 2026, 05:00 Europe/Berlin
1 planned release(s) have already fired with nothing to ship:
- releases.lecture-2 -> course_source_path - [`schedule.yml:88`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L88) - `course-materials-f2026/lectures/02_week-2` does not exist - fires Sun 04 Oct 2026, 06:00 Europe/Berlin

You will get one email about each as its deadline nears.
```

---

## 5. Schedule validator report
`dsl_course/schedule.py:1639-1656` (`_validate_report`, the parse) and `1560-1579`
(`source_report`, the source check) · written to the run's job summary under a
`## schedule.yml` heading and embedded verbatim in the issue in section 3.

```
Parsed ../cohort/schedule.yml
  term 2026-09-07 -> 2026-12-18  (Europe/Berlin)
  16 release(s), 20 deploy(s) | 3 assignment(s) | 4 event(s)

  {n} ENTRY/IES DROPPED:
    - {reason}
```

Then, when the run could resolve the course org (`--check-sources`), either:
```
  {n} SOURCE(S) NOT IN hertie-dsl-demo-course-e1234 YET:
    [{rung}] {entry} -> {field} - {cite} - {what} - fires {when}

  A source you have not written yet looks exactly like this, so this is only
  a fault once its moment is close: advisory until 24h out, then a warning, urgent inside 12h, critical inside 6h, missed once it has fired - at which point it is about to ship nothing.
```
or:
```
  every source in the plan exists in hertie-dsl-demo-course-e1234
```

The verdict line last, and it counts DROPPED entries only - a missing source never fails
the run: `OK: nothing dropped` / `INVALID: {n} entry/ies dropped`, or
`INVALID: {file} could not be parsed` when the whole file is unreadable.

---

## 6. Missing-source digest
`dsl_course/source_digest.py:300-421` (`render_body`, `cleared_body`, `_comment`) · ONE
self-updating issue per cohort in its `classroom-config`, kept in step by every
**Scheduled release** tick (both drivers - GitHub's own cron and the external dispatcher,
see section 8). The body is rewritten every run (a body edit emails nobody); a comment is
posted only when a fault appears at or above WARNING, escalates a rung, or clears; the
issue closes itself when the last fault clears. Silent entirely while nothing has passed
WARNING and no issue is open.

**Title:** `schedule.yml: planned releases cite sources not staged in the course org`

The ladder: ADVISORY (nothing said) -> WARNING (24h) -> URGENT (12h) -> CRITICAL (6h) ->
MISSED (fired). A `.releaseignore`-withheld source is listed and commented on like any
other, but capped at WARNING forever - it never earns anyone an email at 12h/6h or a
"this did not ship", because it is a decision faculty already made on purpose.

**Body** (`render_body`, rewritten whole every run - a missing PATH, a missing assignment
TEMPLATE REPO, and a `.releaseignore`-WITHHELD source, one per rung):
```
`classroom-config/schedule.yml` has broken entries. **Do not close or edit this issue by hand.** Fix the file and this issue closes itself.

cc @hertie-dsl-demo-f2026/instructors

### CRITICAL (6h)

- **releases.lecture-6 -> course_source_path** at [`schedule.yml:142`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L142)  
  `course-materials-f2026/lectures/06_week-6` does not exist yet  |  fix: push the materials to that folder in hertie-dsl-demo-course-e1234/course-materials-f2026, or correct the path on the line above.  
  _fires Sun 04 Oct 2026, 14:00 Europe/Berlin_

### WARNING (24h)

- **assignments.assignment-4-project -> course_source_repo** at [`schedule.yml:210`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L210)  
  assignment-4-project-f2026 does not exist in hertie-dsl-demo-course-e1234  |  fix: create the assignment template repo named on schedule.yml:210 in hertie-dsl-demo-course-e1234 and push the starter files to it, or correct the line above.  
  _fires Mon 05 Oct 2026, 05:00 Europe/Berlin_
- **releases.lecture-9 -> course_source_path** at [`schedule.yml:168`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L168)  
  `course-materials-f2026/lectures/09_week-9` is withheld by a `.releaseignore`  |  fix: remove the pattern, or change course_source_path.  
  _fires Mon 05 Oct 2026, 05:00 Europe/Berlin_

### advisory

- **releases.readings-10 -> course_source_path** at [`schedule.yml:190`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L190)  
  `course-materials-f2026/readings/10_week-10` does not exist yet  |  fix: push the materials to that folder in hertie-dsl-demo-course-e1234/course-materials-f2026, or correct the path on the line above.  
  _fires Sat 10 Oct 2026, 09:00 Europe/Berlin_

---
Field reference: https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/blob/release/docs/07-schedule-releases.md

<!-- dsl-source-state: {"assignments.assignment-4-project.course_source_repo": "warning", "releases.lecture-6[lectures/06_week-6].course_source_path": "critical", "releases.lecture-9[lectures/09_week-9].course_source_path": "warning", "releases.readings-10[readings/10_week-10].course_source_path": "advisory"} -->
<!-- dsl-source-mention: [] -->
```

**Cleared body** (`cleared_body`) - what the issue is left saying once its last fault has
gone, right before it closes:
```
Every entry in `classroom-config/schedule.yml` was usable when this issue closed. It reopens on its own if that changes.

<!-- dsl-source-state: {} -->
<!-- dsl-source-mention: [] -->
```

**Transition comment** (`_comment`) - the only half of this that emails anyone (via section
7). Sections appear only when non-empty; an ESCALATED or CLEARED fault is always said, a
brand-new fault only at the quietest reported rung (WARNING) - a fault that appears
already louder than that has the mail out and the body says it; a comment repeating that
is the noise this design exists to avoid:
```
**Escalated** (closer to its deadline):
- `releases.lecture-6[lectures/06_week-6].course_source_path` is now **CRITICAL** - [`schedule.yml:142`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L142)

**New** (fires within 24h):
- `assignments.assignment-4-project.course_source_repo` - [`schedule.yml:210`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L210)

**Cleared**:
- `releases.lecture-1[lectures/01_week-1].course_source_path` - cleared at 09:00 Europe/Berlin

cc @demo-lkaack-placeholder
```

A quieter tick with only a brand-new WARNING and nobody git could name in `people.yml`
falls back to the team mention:
```
**New** (fires within 24h):
- `assignments.assignment-4-project.course_source_repo` - [`schedule.yml:210`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml#L210)

cc @hertie-dsl-demo-f2026/instructors
```

**Closing comment:** `Every entry in schedule.yml is now usable. Reopens on its own if
that changes.`

Nothing is said between 23:00 and 07:00 in the cohort's own zone: a crossing overnight is
recorded but the comment and the mail wait for the first tick at or after 07:00, and two
rungs crossed overnight are one morning notification.

---

## 7. Fault-mail rungs
`dsl_course/notify.py:348-421` (`_subject`, `_mail`) · one HTML mail per rung a source
fault crosses in section 6's ladder - WARNING, URGENT, CRITICAL, MISSED (never ADVISORY,
never a `.releaseignore`-withheld source). Addressed to whoever git names for the line
(the planner of the `schedule.yml` entry and the last committer of the repo it names),
falling back to the whole teaching team when git can name nobody in `people.yml`. The
toolkit MAINTAINER is copied from CRITICAL up - the two rungs where a release is about to
ship nothing, or already has - never at WARNING/URGENT, which are still faculty's own day.
One message per recipient set per tick; never sent twice for the same crossing, and never
inside the quiet window (see section 6).

**Subject:** `[<course> <tag>] Missing materials: <entry> fires <when> - <n>h left` (or
`fired <when> - nothing shipped` once the moment has passed).

**WARNING** (24h left) - full worked example:
```
Subject: [Deep Learning (Demo) f2026] Missing materials: assignment-4-project fires Mon 5 Oct 05:00 - 24h left
```
```html
<p>This is an automated email sent on behalf of Deep Learning (Demo).</p>
<p>Your schedule.yml plans a release whose materials are not in the <a href="https://github.com/hertie-dsl-demo-course-e1234">course org</a> yet. It will ship nothing to students until they are staged.</p>
<table>
  error line:     schedule.yml:210 - assignments.assignment-4-project -> course_source_repo (linked to the pushed line)
  error content:  the specified repo assignment-4-project-f2026 does not exist or is empty.
  fix by date:    handout fires Mon 05 Oct 2026, 05:00 Europe/Berlin
  to fix:         create the assignment template repo named on schedule.yml:210 in hertie-dsl-demo-course-e1234 and push the starter files to it, or correct the line above.
  GH issue record: https://github.com/hertie-dsl-demo-f2026/classroom-config/issues/7
</table>
```
One such block per fault in the message - two entries the same person is addressed for are
one mail, not two.

**URGENT** (12h left) - identical intro paragraph; only the subject's `Xh left` and the
table's dates move.

**CRITICAL** (6h left) - a louder intro, and the maintainer joins the cc:
```
This release fires within 6 hours and will ship nothing as things stand.
```

**MISSED** (already fired) - subject ends `fired {when} - nothing shipped`; the intro says
the moment has passed, and the row that was "fix by date:" becomes "fired:":
```
This release fired and shipped nothing, because its materials were not in the course org.
```
```
fired:   Sun 04 Oct 2026, 06:00 Europe/Berlin
to fix:  push the materials to that folder now - the next 15-minute tick releases them. Nothing else is needed.
```

Never sent when mail is not configured, or when the tick has no addressable recipient for
any group - the digest's own @mention (section 6) is then the only channel.

---

## 8. Cadence alarms
`dsl_course/cadence.py` · two self-updating issues, STATELESS - both computed fresh every
tick from the `Scheduled release` workflow's own run history, never from a committed
state file. Two audiences, two repos:

- **Driver health** (`_driver_body`/`_driver_comment`, `dsl_course/cadence.py:385-443`) -
  in the course org's PUBLIC `.github`, for whoever runs the infrastructure. GitHub
  delivers its own `schedule` cron best-effort (2-7% of fires, observed gaps to 13h+), so
  a second driver fires the same workflow by `repository_dispatch`; this is the dead-man's
  switch for THAT driver alone.
- **Late delivery** (`_late_body`/`_late_comment`, `dsl_course/cadence.py:451-503`) - in
  each cohort's private `classroom-config`, for that cohort's instructors: named moments
  (a release, a handout, a submission freeze, a solution push) that passed more than an
  hour before the tick that finally shipped them.

Both close on hysteresis (the driver back on cadence, or 8 consecutive healthy ticks),
never on a single good sign.

**Title (driver health):** `Scheduled release: driver health`

```
`Scheduled release` in `hertie-dsl-demo-course-e1234` is not being driven. Every dated action in every cohort rides on these ticks, so while this stands nothing is released, handed out, frozen or graded anywhere.

- last external dispatch: 2026-10-04T06:20:00+02:00 (160 min ago)
- last GitHub cron fire: 2026-10-04T07:55:00+02:00 (65 min ago)

An external dispatch is expected every 15 min and alarms after 120 min. GitHub's own cron line is INFORMATION only - it drops most of its fires by design, so it is never what this issue is about.

Any `repository_dispatch` counts as an external dispatch, a push to a cohort's schedule included: the run listing does not say which sender asked for it.

Read off this workflow's last 20 runs, and rewritten on every tick. It closes itself once a dispatch has arrived within 20 min - back on cadence, not merely seen once.

cc @hertie-dsl-demo-course-e1234/course-admin

<!-- dsl-cadence-state: {"dispatch": "stale"} -->
```

**Transition comment:**
```
- no external dispatch for 160 min (alarm at 120 min)

cc @hertie-dsl-demo-course-e1234/course-admin
```

**Title (late delivery):** `Scheduled release: late delivery`

```
`Scheduled release` in `hertie-dsl-demo-course-e1234` last ran 95 min ago, and these moments in `classroom-config/schedule.yml` came and went inside that gap. Each shipped more than 60 min after the datetime it was written for.

- `releases.lecture-3 -> deploy[0]`: due 2026-10-04T07:40:00+02:00, shipped 2026-10-04T09:00:00+02:00 (+80 min)
- `assignments.assignment-1 snapshot`: due 2026-10-04T07:50:00+02:00, shipped 2026-10-04T09:00:00+02:00 (+70 min)

Nothing above was dropped for being late - this tick is shipping it. The dates are correct as written; the ticks that carry them were late. This issue rewrites itself every tick and closes once 8 consecutive ticks arrive within 20 min of each other.

The gap is measured from the last tick that RAN, whether it succeeded or failed, so a spell of failing runs is reported by the `Scheduled release is failing` issue in `hertie-dsl-demo-course-e1234/.github` rather than here.

cc @hertie-dsl-demo-f2026/instructors

<!-- dsl-cadence-state: {"items": ["assignments.assignment-1 snapshot", "releases.lecture-3 -> deploy[0]"]} -->
```

**Transition comment:**
```
**Shipped late**:
- `releases.lecture-3 -> deploy[0]`: due 2026-10-04T07:40:00+02:00, shipped 2026-10-04T09:00:00+02:00 (+80 min)
- `assignments.assignment-1 snapshot`: due 2026-10-04T07:50:00+02:00, shipped 2026-10-04T09:00:00+02:00 (+70 min)

cc @hertie-dsl-demo-f2026/instructors
```

Neither alarm sees a streak of red runs that finally ships a moment - that is section 2's
`<workflow> is failing` issue, from the first red run.

---

## 9. Repo descriptions
Short strings, but they are the "What it's for" column on both org landing pages. A
description is set at repo CREATION only; earlier wordings are rewritten by
`repos.converge_descriptions` off the `SUPERSEDED_*` tables (`repos.py:134-183`), so an
edit here reaches existing orgs only if the old string is added to that chain.

The CURRENT wording of every repo the pipeline seeds:

| Repo | Text | Source | Seen by |
| --- | --- | --- | --- |
| course org `.github` | `[control panel]: Org profile & configuration` | `bootstrap_course.py:423` | faculty |
| cohort org `.github` | `[do not touch]: Org profile and configuration` | `bootstrap_course.py:423` | both |
| cohort `welcome` | `Course front door - open a Join issue to enrol` | `bootstrap_course.py:506` | student |
| cohort `classroom-config` | `[visible to instructors only]: Everything you configure for this cohort is here - student roster, teams, term schedule, and marking. Students never see it, and no PII leaves this repo.` | `bootstrap_course.py:542` | faculty |
| course `course-materials-<tag>` | `Course materials (lectures/labs/readings/datasets/other) by session` | `scaffold.py:412` | faculty |
| course `assignment-N-<tag>` | `Assignment {number} template` | `scaffold.py:473` | faculty |
| `<org>.github.io` (both tiers) | `[do not touch]: Course website (auto-deployed)` | `scaffold.py:679` | both |
| cohort `materials` | `Released lectures, labs, readings, & other materials` | `deploy.py:218` | student |
| cohort `<slug>-<handle>` | `{slug} - submission repo` | `assign.py:393` | student |
| cohort `grades-<handle>` | `Private gradebook for @{handle}` | `grades.py:2158` | student (own repo only) |
| cohort assignment template | `{slug} - cohort assignment template` | `assign.py:238` | faculty |

Superseded wordings still being converged (left) -> current (right), `repos.py:134-183`:

| Old text still in the wild | Rewritten to |
| --- | --- |
| `Released course materials (enrolled students only)` | `Released lectures, labs, readings, & other materials` |
| `Released lectures, labs, readings, and other materials` | `Released lectures, labs, readings, & other materials` |
| `Course materials (lectures/readings by session)` | `Course materials (lectures/labs/readings/datasets/other) by session` |
| `Course website (auto-deployed on push)` | `[do not touch]: Course website (auto-deployed)` |
| `Cohort course website (auto-deployed on push)` | `[do not touch]: Course website (auto-deployed)` |
| `Org profile and configuration` (cohort org) | `[do not touch]: Org profile and configuration` |
| `Org profile and configuration` (course org) | `[control panel]: Org profile & configuration` |
| `PRIVATE cohort config - roster (students.csv). No PII leaves here.` | the `[visible to instructors only]: ...` text above |

## 10. Team and label descriptions
Team descriptions show on each team's page; the two labels show on every Join issue.

| Team / label | Text | Source |
| --- | --- | --- |
| `instructors` (every org) | `Instructors and TAs` | `course.py:67` |
| `course-admin` (every org) | `Course administrators - DSL team` | `course.py:68` |
| `students` (cohort, secret) | `Enrolled students` | `course.py:81` |
| `auditors` (cohort, secret) | `Auditors - read-only (released materials only, no assignments)` | `course.py:84` |
| `instructors-<tag>` (course org) | `Instructors for {tag} (cohort-declared)` | `access.py:140`, `sync_faculty.py:337` |
| per-team (cohort) | `Project team (auto-managed from teams.csv)` | `sync_teams.py:83` |
| label `onboarding` | `Join course issue - routes the Onboard student workflow` | `welcome.py:131` |
| label `team-formation` | `Join team issue - routes the Form team workflow` | `welcome.py:132` |

## 11. Commit messages in student-readable repos
Low-visibility but permanent in history: `grades: update`, `init gradebook`,
`add solution`, `release: sync materials into {repo}`, and - one commit, once per
**Distribute grades** run - `grades: distribute ({n} comment(s), {n} gradebook(s), {n}
email(s))` (`grades.py:2775-2778`).
