# Notifications - issues and PRs the system opens

Prose the system writes into GitHub issues and pull requests. Faculty read all of these.
Edit the text; I'll port it back to the generator named under each.

---

## 1. Grades preview PR
`dsl_course/grades.py:446-452` · opened in `classroom-config` by **Render grades**

**Title:** `Grades: review before distribution`

```
Rendered {n} gradebook(s) from `grades/`.

**This is the preview.** Review every student's grades in the diff below, then merge to distribute to each private `grades-<handle>` repo.
```

---

## 2. Site-sync overwrite notice
`dsl_course/site.py:795-819` · opened in the cohort's site repo when a sync replaces a
hand-edited generated file. Deduped by title; commented on if already open.

**Title:** `Manual edits to generated site files are overwritten by the sync`

```
The site sync regenerates parts of this repo from the org structure, so an edit made directly here is replaced the next time it runs. It has just replaced:

- `{path}` - edited by @{login} in [`{sha}`](https://github.com/{org}/{site}/commit/{sha})

Nothing is lost - each link above is the commit that was overwritten, so the change can be copied back out of it.

Make the edit at the source instead, and it survives every sync:

- **Staff cards** - the cohort's `classroom-config/people.yml` (for a public course site, the `people:` block of the course org's `.github/dsl-course.yml`).
- **Schedule rows, sessions, assignments** - the org structure and the cohort's `classroom-config/schedule.yml`.

The sync owns `_lectures/`, `_assignments/`, `_events/`, `_data/people.yml` and a few `_config.yml` keys, and names the source in a header where the file format allows one. Everything else in this repo is yours and is never rewritten.
```

Appended only when a commit author's git email maps to no GitHub account:
```
cc @{org}/instructors - a commit author's email is not linked to a GitHub account, so they could not be mentioned directly.
```

---

## 3. Unattended cron failure
`dsl_course/workflows_render.py:167-209` · appended to every cron-bearing workflow
(Sync membership, Sync site, Scheduled release, Refresh actions, Publish site). Filed in
the repo the workflow runs in; closes itself on the next green run.

**Title:** `{workflow name} is failing`

```
The unattended run failed or was cancelled (a timeout counts): {RUN_URL}

Nothing retries it before the next scheduled run. This issue closes itself once a run succeeds.

cc @{org}/course-admin
```

**Closing comment:** `Recovered: {RUN_URL}`

---

## 4. Schedule validation failure
`templates/classroom-config/validate-schedule.yml:94-113` · filed on a push that breaks
`schedule.yml`, assigned to whoever pushed.

**Title:** `schedule.yml has entries the scheduler cannot read`

```
Validation of `schedule.yml` failed.

​```
{validator report - see section 5}
​```

Commit: {SHA}
Run: {RUN_URL}

A dropped entry is silently absent from the term plan - no release, or no deadline, snapshot or autograding. Fix `schedule.yml` on `main` and this issue closes itself.

Field reference: https://github.com/{central}/blob/main/docs/07-schedule-releases.md
```

**Closing comment:** `schedule.yml now parses with nothing dropped.`

---

## 5. Schedule validator report
`dsl_course/schedule.py:808-827, 1043-1047` · written to the job summary and embedded in
the issue above.

```
Parsed {source}
  term {start} -> {end}  ({timezone})
  {n} release(s), {m} deploy(s) | {n} assignment(s) | {n} event(s)

  {n} ENTRY/IES DROPPED:
    - {reason}
```
Then one of: `OK: nothing dropped` / `INVALID: {n} entry/ies dropped`

---

## 6. Repo and team descriptions
Short strings, but they show on GitHub org and team pages - students see the first three.

| Text | Source | Seen by |
| --- | --- | --- |
| `Released course materials (enrolled students only)` | `deploy.py:135` | student |
| `Private gradebook for @{handle}` | `grades.py:302` | student |
| `{slug} - submission repo` | `assign.py:208` | student |
| `Project team (auto-managed from teams.csv)` | `sync_teams.py:72` | student |
| `{slug} - cohort assignment template` | `assign.py:99` | faculty |
| `Course front door - open a Join issue to enrol` | `bootstrap_course.py:490` | student |
| `PRIVATE cohort config - roster (students.csv). No PII leaves here.` | `bootstrap_course.py:524` | faculty |
| `Course materials (lectures/readings by session)` | `scaffold.py:123` | faculty |
| `Org profile and configuration` | `bootstrap_course.py:378` | both |
| `Course website (auto-deployed on push)` | `scaffold.py:460` | both |

## 7. Commit messages in student-readable repos
Low-visibility but permanent in history: `grades: update`, `init gradebook`,
`add solution`, `release: sync materials into {repo}`.
