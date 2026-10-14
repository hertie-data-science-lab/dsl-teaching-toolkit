# Changelog

## 0.11.0 (unreleased)

- One kind model: the policy's `kinds` gain `plural`, `numbered` and `shown`, the `term` kind is
  `semester`, and every schedule row renders through one `schedule_row.html` (the per-kind row
  includes are retired from each semester site on its next Sync site).
- Old names live in one module, `legacy.py`, which every parser and the migration read. The
  0.10.0 recognition is gone (every live org migrated 13-14 Oct): retired `schedule.yml` and
  `dsl-course.yml` keys are unknown keys, `people.yml`, the old registry file, the `dsl-cohort`
  topic, `submit_via: github`, `grading.yml`, the old CLI flags, request fields and
  `cohort_org` dispatch are no longer recognised, and the console has no migration screen and
  no old-hash redirects. ds01's `all_cohorts` stays an alias until ds01-infra switches.
- `DSL_COURSE_ADMIN_EMAILS` is gone: a fault in a course's own config is mailed to the admins'
  `email:` in `dsl-course.yml`.
- A run setting in a template's `grading_config.yml` is refused as `RUN_SETTING_IN_TEMPLATE`.
