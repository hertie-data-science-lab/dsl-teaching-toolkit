# Changelog

## 0.11.0 (unreleased)

- Releases are fire-once (decision 0043): each copy ships once, at its time, and is recorded
  in `semester-config/.system/releases.json` with its source commit, ship times and file
  manifest. An edit to a released course folder reaches students on Release again, or on
  every tick where `autosync_released_edits` is true. A due copy already at its destination
  is recorded without being copied, so no semester re-ships on upgrade. The status reads a
  recorded copy as released whatever its destination holds; the preview says
  `ALREADY_DONE` for an entry whose copies are all out.
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
