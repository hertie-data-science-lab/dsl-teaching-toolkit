# Changelog

## Unreleased

- A course materials repo's settings page has a **Files** panel in place of the Withheld panel:
  the repo's tree with **Browse** (a name opens the file beside the tree), **Withhold** (untick
  what never reaches students; the pattern box and Save as before) and **Release** (tick files
  or folders and release them into a chosen semester now). A splitter sizes the two columns.
- The semester's **Materials** page folds Released materials this semester and adds **Release
  by hand** below it: From a course repo, ticks in its tree, and a manifest of what lands where,
  each folder group with its own Edit destination. Each file carries a chip against the
  semester: `up to date`, `behind`, `ahead`, `diverged`, `not released` or `withheld`; behind,
  ahead and diverged open a popover with one action. One **Release** copies every pick, from
  several course repos into several semester repos, in one run (`release.adhoc` takes comma
  lists of repos paired in order with the paths). **Schedule on the calendar** opens a new
  Schedule entry with the same files. The tree rows no longer link their Release page.
- Course details and Semester defaults gain a **Releases** section: Release from (the
  `dsl-materials` topic), Release into (`release_destinations`, a new `defaults:` list, a
  semester's adding to the course's) and **Default destination** (`materials_repo`, relabelled
  from "Repo a release lands in" and moved out of the Assignments fold). The console names the
  semester's effective default, not `materials`.
- PDFs draw inline in the viewer (the console's policy allows `frame-src blob:`), with Open in a
  new tab and Download under them.
- The side nav hides behind a handle on its edge; the choice is kept per browser.
- A page shows an operation's verb alone; its panel offers the preview, never requires it.
- The institution's withheld names split in two. **Never released** (`.git`, `.system`, `.env`,
  `.env.*`, `grading_config.yml`, `grading.yml`): no route ships one, and naming one outright is
  refused. **Content** (`solution`, `solutions`, `tests`): withheld from a folder release unless
  a `!` line in the repo's `.releaseignore` re-includes it; naming one outright still ships it.
  In the Files panel these rows show `withheld` like a line's, footnoted "withheld by default";
  in Withhold they start unticked, and a tick writes the `!` line ("released by `!solutions/`").
  The public website keeps the content group off whatever a `!` line says.
- Hand releases and Release again take an **Overwrite** option (`replace`): the course version
  of the copied paths goes onto the branch students read instead of being merged, so a semester
  edit or removal is undone without a pull request.
- **Keep for future semesters** and **PR into source** can carry back named paths (`paths`),
  hand and early releases included; successive per-path runs add to the open proposal, while a
  full run regenerates it.
- One **Open** split button for repos and files in both consoles: the main part opens, the
  caret lists In the browser, On your computer and Change your profile.

## 0.11.0 (unreleased)

- The late window and the late penalty inherit each on its own, like every other default
  (they were one pair: a layer naming one half set the other to none). Setting only the
  window keeps the inherited penalty, and vice versa; `late_window_days: 0` is still no late
  work. The migration writes a 0.10 layer that named one half with the other as none
  (`0` / `0%`). A bare number in a penalty is always a percentage, in the files and the
  console alike: `10` is 10% and `0.5` is 0.5% (a bare number below 1 was a fraction, and
  one of 1 or more was refused). This covers `late_penalty_per_day` and a unit's
  `late_penalty`. The console's penalty fields save a plain number with its `%`.
- The console's semester nav has **Materials** after Schedule: Released materials
  (`?cohort=<org>#materials`), the semester's materials repos as students have them, each
  file opening in the viewer, each released folder linked to its Release page and a released
  copy edited in the semester said in the Dashboard's words. The course nav's Course
  materials link now names the course (`?course=<org>#materials`).
- The Updates box dates a released row by the release record (decision 0043): a released
  `tbc` row is announced at the moment it first shipped (the site's `date:` and `released_at:`,
  the student status's `rows[].released_at`), where before every dateless row was dropped. A
  released `tbc` entry offers its session date (`tbc -> [date]`) on its console Release page
  and is a suggested to-do until dated. A new Semester defaults page in the semester nav (between
  Site and Operations) edits the run defaults (with the `defaults:` timezone),
  `autosync_released_edits` and the semester dates; the old `#settings` link lands there. The
  same form opens as a modal from the Assignments, Schedule and Site pages, at the section that
  fits each.
- `schedule.yml` names no `timezone:` (decision 0039 rule 2): a semester's times are read in its
  `semester.yml` `defaults: timezone`, cascaded. The migration's config layers step moves a
  schedule's `timezone:` there; left in the file it is NOT_MIGRATED and ignored. The Schedule
  screen's dates sheet no longer asks for one.
- A release of paths ahead of a copy's planned moment is that copy's release (decision 0043):
  it is recorded, so the scheduler does not copy it again, and its `release_datetime` becomes
  the time it went out.
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
  `email:` in `course.yml` `people.course_admins`.
- A run setting in a template's `grading_config.yml` is refused as `RUN_SETTING_IN_TEMPLATE`.
- `schedule.yml` has one block per kind (`lectures:`, `labs:`, `readings:`, `exams:`,
  `drop-ins:`, `events:`, `assets:`) beside `assignments:`, and it is the only source of
  rows: a lecture or lab names its unit with `session:`, `number:` is explicit, slots replace
  second-group events, `files:` (`from_course_repo`, `from_path`, `to_semester_repo`,
  `to_path`, `release_datetime`) replaces `deploy:`, a readings entry's `reading_guide:`
  replaces `READINGS.md`, and an assignment's run settings sit on its entry. Folder names
  and `materials.yml` `kinds:` no longer mean anything. The migration's **schedule shape**
  step rewrites each semester's file (decision 0038).
- The term's own dates are one `semester:` block whose entries name their `type` (`start`,
  `end`, `break`, `archive`); a break is a grey band on the site and its weeks are never
  empty. `assets` entries default off the site; an
  assignment entry may override its template's `type` and `submit_via` (decision 0038
  amendment 2).
- `autosync_released_edits` joins the `defaults:` cascade (semester > course > institution;
  institution `false`): whether an edit to an already-released course folder re-ships on the
  next scheduled run. The console's course and semester defaults offer it as "Send fixes to
  released files automatically" (decision 0043).
- One hand-edited file per level (decision 0039): `institution.yml` (was `policy.yml`),
  the course's `.github/course.yml` (was `dsl-course.yml` + `opencourse.yml`;
  `central_ref` is `toolkit_version_ref`), and the semester's `semester-config/semester.yml`
  (was `instructors.yml`, `assignments.yml` `defaults:` and `materials.yml` `syllabus:`).
  `defaults:` cascades per key (entry > semester > course > institution). `students.csv` is
  `email,name,role` plus one `team:<number>` column per team assignment; the engine's columns
  move to `.system/roster.csv` and `teams.csv` goes. The pointer is `.system/course.yml`. The
  migration's **config layers** step folds each org's files.
- One Selection shape (`kinds`, `types`, `include`, `exclude`) answers every "which files"
  question (decision 0040): a materials repo's `.releaseignore` (what students never get),
  `semester.yml` `student_site.host` and `course.yml` `open_course_site.publish`.
- Faculty use the console or the CLI (`python -m dsl_course <op>`, the same ops; decision
  0041): the per-op Actions buttons and the per-repo Release workflows are retired, and
  `semester-config` has one `dispatch.yml`.
- A read GitHub answers with an HTML error page (`invalid character '<'`) is retried like an
  HTTP 500.
