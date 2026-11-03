# Changelog

## 0.11.0 (unreleased)

- Releases are fire-once (decision 0043): each copy ships once, at its time, and is recorded
  in `semester-config/.system/releases.json` with its source commit, ship times and file
  manifest. An edit to a released course folder reaches students on Release again, or on
  every tick where `autosync_released_edits` is true. A due copy already at its destination
  is recorded without being copied, so no semester re-ships on upgrade. The status reads a
  recorded copy as released whatever its destination holds; the preview says
  `ALREADY_DONE` for an entry whose copies are all out.
- A released folder that differs from what was released is told, per kind (decision 0043
  rule 6): the course copy changed, files deleted in the course and still released, or the
  released copy edited. Each is a set-aside-able suggestion on the Dashboard and a panel on
  the release's page with its files; Release again can also remove the files deleted from
  the course copy (`deploy --remove-deleted`). The record keeps a file a re-copy did not
  overwrite, since it is still at the destination.
- A copy released before its time, or from a `tbc` entry, has the time it went out written
  into its `files:` item's `release_datetime` (one bot commit per release; nothing for an
  on-time or late release or a Release again). A hand-out run earlier than its
  `handout_datetime` moves it to the actual moment, once. A copy released by hand in time is
  never reported as a late delivery.
- One kind model: the policy's `kinds` gain `plural`, `numbered` and `shown`, the `term` kind is
  `semester`, and every schedule row renders through one `schedule_row.html` (the per-kind row
  includes are retired from each semester site on its next Sync site).
- Old names live in one module, `legacy.py`, which every parser and the migration read. The
  0.10.0 recognition is gone (every live org migrated 13-14 Oct): retired `schedule.yml` and
  `dsl-course.yml` keys are unknown keys, `people.yml`, the old registry file, the `dsl-cohort`
  topic, `submit_via: github`, `format:`, the registry's `cohorts:` key, `grading.yml`,
  `notified.csv`, `_publish-config.yml`, an untopicked materials repo, the old CLI flags,
  request fields and `cohort_org` dispatch are no longer recognised, and the console has no migration screen and
  no old-hash redirects. ds01's `all_cohorts` stays an alias until ds01-infra switches.
- `DSL_COURSE_ADMIN_EMAILS` is gone: a fault in a course's own config is mailed to the admins'
  `email:` in `dsl-course.yml`.
- A run setting in a template's `grading_config.yml` is refused as `RUN_SETTING_IN_TEMPLATE`.
- `schedule.yml` has one block per kind (`lectures:`, `labs:`, `readings:`, `exams:`,
  `drop-ins:`, `events:`, `assets:`) beside `assignments:`, and it is the only source of
  rows: a lecture or lab names its unit with `session:`, `number:` is explicit, slots replace
  second-group events, `files:` (`from_course_repo`, `from_path`, `to_semester_repo`,
  `to_path`, `release_datetime`) replaces `deploy:`, a readings entry's `reading_list:`
  replaces `READINGS.md`, and an assignment's run settings sit on its entry. Folder names
  and `materials.yml` `kinds:` no longer mean anything. The migration's **schedule shape**
  step rewrites each semester's file (decision 0038).
- The term's own dates are one `semester:` block whose entries name their `type` (`start`,
  `end`, `break`, `archive`); a break is a grey band on the site and its weeks are never
  empty. `reading_list` is `reading_guide`; `assets` entries default off the site; an
  assignment entry may override its template's `type` and `submit_via` (decision 0038
  amendment 2).
- `autosync_released_edits` joins the `defaults:` cascade (semester > course > institution;
  institution `false`): whether an edit to an already-released course folder re-ships on the
  next scheduled run. The console's course and semester defaults offer it as "Send fixes to
  released files automatically" (decision 0043).
