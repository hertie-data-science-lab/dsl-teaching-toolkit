# Faculty & instructors actions reference

Every operation is a button in the **Instructor Console** and the same command from a laptop:
`python -m dsl_course <op> --course-org <org> [--semester-org <org>] [--arg key=value ...]`,
in a checkout of this toolkit with `gh` signed in. A command previews unless given
`--no-preview`; `python -m dsl_course` lists every op and `python -m dsl_course <op> --help`
its arguments. Both run the same request and report the same outcome sentence.

The course org's **`.github` Actions tab** keeps only the automatic workflows and the two
grading workflows the Console starts; content repos and assignment templates carry none.

Step-by-step flows: [workflow runbooks](../README.md). File layouts and CSV columns:
[`DEPLOYMENT-CHECKLIST.md`](../DEPLOYMENT-CHECKLIST.md). Who may run what, and which team
grants it: [`access-reference.md`](access-reference.md).

## Setup

| Action | Op | Effect |
| --- | --- | --- |
| **Set up** (New semester; course admins) | `semester.bootstrap` | Configure a pre-created semester org: `join` + `semester-config`, permissions, site, `course_admins`, register + refresh. Safe to re-run on a live semester - your `semester-config` files are never overwritten. |
| **New course materials** | `materials.create` | Scaffold a `course-materials-<semester>` repo (lectures/readings/labs session folders, `SYLLABUS.md`). `copy_from` starts it as an existing materials repo instead - every branch, the whole history - only the toolkit's own files are rewritten. |
| **New assignment** | `assignment.create` | Scaffold an `assignment-<name>` template from its answers (name, formats, type, submit_via, autograde, starter): brief + starters (+ `CONTRIBUTIONS.md` for a group) on `main`; stub solutions and `grading_config.yml` on the `solution` branch, with a hidden-test stub only when autograding was asked for. `formats` takes a comma-separated list and picks the starter stubs and nothing else; `type: group` makes handout + grading run per team; `submit_via: external` creates no repos at all, and `submit_via: shared_dropbox_repo` creates one `<slug>-submissions` drop box for the whole semester with a folder per student; How each semester runs it (team formation, max team size, the late rule, visibility) is that semester's `assignments.yml`, and its number is that semester's schedule's ([03](../03-add-assignment-to-course.md)). |
| **Derive student version** | `assignment.derive_starter` | Write a template's student starter onto `main` from the ONE notebook you keep on its `solution` branch: `### BEGIN SOLUTION` / `### END SOLUTION` regions, `solution`-tagged cells and `solution=TRUE` Rmd chunks are replaced with placeholders, and a stripped code cell loses its stored outputs. It never writes to `solution`, and a file with nothing fenced in it is never written at all (a blank file, such as an empty `__init__.py`, is copied as it is). A template whose `starter:` is `handwritten` (or that marks nothing and has no `starter:`) is refused: *This template's starter is written by hand, so nothing is derived.* A preview prints the file list and counts only. See [derived or by hand](../assignment-starter.md). |
| **Weekly plan** | `assignment.generate_syllabus` | Write the weekly plan into your syllabus - one block per session, with its title, learning objectives and reading list - from a semester's `schedule.yml`. Only the block between `<!-- dsl:weekly-plan -->` and `<!-- /dsl:weekly-plan -->` changes; without those lines it adds them under "## Weekly plan" at the end. Move the block anywhere in the file and the next run updates it there. |
| **Refresh** | `semester.check` | Check the semester's setup - what's configured and what's missing, and which of its fault issues are open - and refresh what the Console shows. |
| **Refresh actions** (workflow) | - | Re-render the org's workflows, propagate the repo secret, repopulate every dropdown, rebuild the profile READMEs, and delete any workflow the toolkit has retired. No inputs. Also runs itself daily, so every org converges on the toolkit tier its course org runs within 24h without anyone clicking. _(All DSL orgs at once: [Refresh Course Orgs Inventory](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/actions/workflows/refresh-inventory.yml).)_ |
| **Console** (workflow) | - | No button to press: runs what the Instructor Console asks for, one request per run. A run that breaks (not an op that says no, and not a Stop) files a *Console is failing* issue and emails the maintainer; the next good run closes it. |
| **Sync membership** (workflow) | `access.check` for one semester | Reconcile `students`/`auditors` teams (`students.csv`), project teams (`teams.csv`) and instructor/course-admin access (`instructors.yml`, `dsl-course.yml`), and rewrite `assignments.lock.yml` - the generated mirror the **Join team** form reads (hence the `schedule.yml` trigger). Automatic on push to any of those files, plus a daily cron; a manual run walks every semester, like the cron - run it by hand only to apply a `start`/`end` date that rolled over without an edit. A push to the course org's own `dsl-course.yml` or `semesters.yml` instead checks just those two and reports them; a semester whose files it cannot use is skipped and reported, not failed. See [05](../05-manage-teaching-team.md), [`access-reference.md`](access-reference.md). |

## Release

| Action | Op | Effect |
| --- | --- | --- |
| **Scheduled release** (workflow) | `semester.preview_automation` previews it | **Primary** - fires the semester's plan and freezes passed deadlines, roughly every 15 minutes. Manual runs default to `preview=true`. See [07](../07-schedule-releases.md#what-drives-the-scheduler). |
| **Release** | `release.entry`, `release.adhoc` | `release.entry` ships one schedule entry's files now; `release.adhoc` copies `course_source_path` from a course-org `course_source_repo` into the semester's `semester_dest_repo` at `semester_dest_path` - a `schedule.yml` `files:` item's `from_path`, `from_course_repo`, `to_semester_repo` and `to_path`. Covers session folders, datasets, root files and code subpackages alike. _Fallback - see [07](../07-schedule-releases.md), [08](../08-release-materials-to-cohort.md)._ |
| **Hand out** | `assignment.handout_now` | Freeze a semester template from the chosen `assignment-*`, then generate one private `<key>-<handle>` repo per onboarded student. It takes the template and `solution_datetime` (empty; `now` pushes the model answer and rubric, which cannot be undone, so it runs only straight after the same person's preview of it). Individual or group is the template's `grading_config.yml`, and a template two schedule entries hand out from is refused: release that one from the schedule. `submit_via: external` creates no repos: it records the handout, writes the grading sheet and the gradebooks, and publishes the brief. `submit_via: shared_dropbox_repo` creates one `<key>-submissions` instead of one repo per student, grants push on it to every onboarded unit, and protects it against force-push and deletion. _Fallback - see [07](../07-schedule-releases.md), [09](../09-release-assignment-to-cohort.md)._ |
| **Update every copy** | `assignment.update_copies` | Push a corrected file (or folder) from the template's default branch into every submission repo of an assignment already handed out - a new commit on each student's branch, never a force-push - and post a note on each submission-receipts issue. A file the student has already changed is kept unless `overwrite` says otherwise; the frozen semester-side hand-out is patched too, so later onboarders get the fix. See [09](../09-release-assignment-to-cohort.md#fixing-a-file-after-the-assignment-has-gone-out). |
| **Email students without a team** | `teams.open_window` | Email every student still without a team for an assignment whose team-formation window is open - the cap, the day formation closes, a link to the semester's **Join team** form. The scheduler sends this by itself when a window opens, and again 48 hours before it shuts; this is how you send it again on your own say-so. Nothing is said twice, so a second run reaches only whoever the first could not. A key with no open window is an error, not a quiet no-op. Held between 23:00 and 07:00 in the semester's timezone. Mailing needs the `GRAPH_*` secrets. See [09](../09-release-assignment-to-cohort.md#group-assignments-creating-the-teams). |
| **Send enrolment codes** (workflow) | `roster.send_codes` resends | **No button** - it runs only on a push to a semester's `students.csv`, and it sends for real. Generates an `enrol_code` per roster row, writes it back to `students.csv`, emails each not-yet-onboarded student theirs. Safe on every push: a row's `code_sent_at` stops it being mailed twice (from the first run that sets it), so a re-send means clearing that cell and pushing - or the Console's *Send new codes*, which replaces every unjoined row's code. A roster the toolkit cannot read leaves the run green and is reported in the semester's digest issue instead; a real failure files an issue and emails the maintainer, like the crons. See [06](../06-enrol-students-to-cohort.md). |
| **Sync site** (workflow) | `site.update` for one semester | Regenerate every semester's website. Releases, a push to `schedule.yml` and a daily cron already do this for you. |

## Grades

Full flow: [Grade and return assignments](../10-grade-and-return-assignments.md).

| Action | Op | Effect |
| --- | --- | --- |
| **Collect submissions** (workflow) | `assignment.collect_now` | Refresh one assignment's grading sheet now instead of waiting for the cron: re-read each submission, refill `info:`, post any receipt still owed. It never freezes anything - the cutoff does that - and runs no student code; grading is the scheduler's. Before the due date there is nothing to derive yet, so a press then says so and refreshes nothing. The Console starts this workflow. |
| **Distribute grades** (workflow) | `grades.return` | Send what the grading sheet holds to the three places it goes: each student's private `grades-<handle>` repo (`grades.yml` + `README.md`), `.system/semester-grades.csv` (the semester grades file), and an email. Nothing is posted into a submission repo. Nothing is said twice, so a re-run after one correction reaches one student. A mark distributed before the due date is sent with a warning, since the submission facts behind it are not derived yet. **`preview` defaults to `true`**: it writes no grades, sends no mail, and posts who gets what as a *Distribute grades preview* issue in `semester-config`, which the real run closes. Untick `notify` to send without emailing. `assignment` empty returns every sheet; named, only that assignment's marks go out, and each gradebook still shows every assignment already returned. The Console's **Return marks** starts this workflow, and every real run of a semester queues behind the last, the automatic return's included; a preview queues behind nothing. |

<a id="end-of-term"></a>

## End of semester

| Action | Op | Effect |
| --- | --- | --- |
| **Keep for future semesters** | `release.propagate_back` | Carry the semester's edits to released material back into the course org. For every path already released to it, what the semester has now is copied over the course org's copy on a branch `from-<semester-org>`, one commit per path, and one pull request per source repo asks faculty to merge, cherry-pick or close. **Deletions are not propagated** - a file the semester dropped is named in the pull request and left where it is. The branch is cut fresh and force-pushed on every run, so each run proposes what the semester has then. Archive runs it first. See [08](../08-release-materials-to-cohort.md#carrying-semester-edits-back). |
| **Archive** | `semester.archive` | Archive a finished semester early, or at all. The scheduler does this by itself only for a semester whose `schedule.yml` `semester:` block has a `type: archive` entry (its `event_datetime:` defaults to the end + 60 days), after a fortnight's notice by issue and email; this is the manual way, and the only way for a semester without one. It propagates the semester's edits back first, closes the toolkit's open notices, syncs the website one last time, then archives **every repository in the org** - students' work, the materials, `join`, the website, `.github` - records what was archived in `semester-config/.system/archive.md`, and archives `semester-config` last, which also tells every nightly sync to leave the semester alone. **Nobody is removed and nothing is deleted**: an archived repo is read-only for everyone, students keep read access, and un-archiving from a repo's Settings brings it back. A real run refuses until the archive date has arrived, and `force` overrides that. Safe to re-run - it resumes. See [10](../10-grade-and-return-assignments.md#archiving-the-semester). |

## Optional: public course website

| Action | Op | Effect |
| --- | --- | --- |
| **Publish course website** (workflow) | `course.publish_website` | Build/refresh a **public** `<course-org>.github.io` sharing one materials repo's lectures + readings, as the course's `.github/opencourse.yml` says (edit it on the console's Public website tab): `enabled`, `source_repo`, `readings_mode` = `reading-list` (citations only, default), `actual-readings` (host the files) or `none`, `include_lectures`, and `withhold` (paths kept off the site, `.releaseignore` syntax). No inputs; a daily cron re-publishes from the file. `enabled: false` (the default) stops both. |

## Why did I get this email?

Every file you edit by hand is checked, and anything the toolkit cannot use opens **one
issue per file** and emails whoever committed that line. Fix the line and the issue closes
itself; nothing here reds a run.

| The issue you were cc'd on | is about | explained in |
| --- | --- | --- |
| *schedule.yml: planned releases cite sources not staged in the course org* | a release naming a folder nobody has staged, any entry the scheduler had to drop, a file that does not parse at all, and a group assignment whose teams have not all formed, which stays listed after the window shuts | [07](../07-schedule-releases.md#the-digest-issue) |
| *instructors.yml has entries the sync cannot use* | a teaching-team entry that grants nobody access, or that nobody can be emailed at | [05](../05-manage-teaching-team.md) |
| *students.csv has rows the toolkit cannot use* | a roster row - or a whole file - the enrolment cannot read | [06](../06-enrol-students-to-cohort.md) |
| *teams.csv has rows the toolkit cannot use* | a project-team row that will not be acted on | [09](../09-release-assignment-to-cohort.md) |
| *grading sheets have entries the grader cannot read* | a mark, key or unit in `grading_sheets/` that stops a return | [10](../10-grade-and-return-assignments.md#when-a-sheet-has-something-nobody-can-act-on) |
| *assignment grading_config.yml has values that will not grade as written* | an assignment definition that will not grade the way it reads | [03](../03-add-assignment-to-course.md) |
| *assignments.yml has values that will not run as written* | a semester run setting that no longer describes the repos it handed out | [09](../09-release-assignment-to-cohort.md) |
| *dsl-course.yml / semester registry has entries the sync cannot use* (earlier: *... / cohort registry ...*) | the course org's own two files, which decide whether the course is synced at all | [01](../01-new-course-org.md) |

The first seven live in that semester's private `semester-config` and cc its `instructors`; the
last lives in the course org's `.github` and cc's `course-admin`.

**When it reaches you.** A fault tied to a *moment* - a source a release needs, a value used
to grade - gets louder as that moment nears (24h, 12h, 6h, and once more if it passes), and
is never emailed between 23:00 and 07:00 in the semester's timezone. A fault that is simply
unreadable is emailed the minute it is pushed, again if it is still there after two days, and
again after a week - then the issue stands and says no more.

**Who gets it.** Whoever git says edited that line, or pushed that CSV. A TA's email copies
the semester's instructors; if git can name nobody, every instructor is emailed.
Addresses come from `email:` in `semester-config/instructors.yml`; if nobody there has one, it
goes to the course admins, and to the toolkit maintainer if the course names none. A row or
line and a column are named - never a cell value, never a student.

The Console's **Refresh** (`semester.check`) lists which of these are open right now.

## When a scheduled run fails

The five scheduled actions (Scheduled release, Sync membership, Sync site, Refresh actions,
Publish course website) run with nobody watching - and so does **Send enrolment codes**, which
has no schedule and no button at all: a push to a semester's roster fires it. A failure in any of
the six **opens an issue in your `.github` repo** titled *"&lt;action&gt; is failing"*, with a
link to the run and a cc to your org's `course-admin` team, so it reaches an inbox. It comments on
that same issue while the failure persists, and closes it as soon as a run succeeds - so an
open one always means "still broken". Don't close it by hand; fix the cause and re-run the
action. A file **faculty** have to fix never opens one of these - a file that is missing, one
saved in the wrong format, a row naming somebody who is not on the roster: Sync membership
skips that semester and Send enrolment codes sends nothing, both stay green, and the fault is
reported in that semester's own digest issue and emailed to whoever left it there - see above.

**Scheduled release** (every 15 minutes) and **Sync membership** (hourly) run so often that a
single failed run is usually a passing GitHub fault. They open the issue only when a run fails
**twice in a row**; a failure after a good run says so in its log and waits for the next run.

On the same throttle, the **toolkit maintainer is emailed** the run's URL and the last 30
lines of the step that failed. A broken run is infrastructure rather than teaching, and the
issue says so: nothing here is for instructors to do. The two channels fire together or
not at all - an email always has an issue behind it, and a thread being kept quiet for six
hours sends no email either.

**Scheduled release** reports per job, because it releases and grades separately: the release
pass keeps *"Scheduled release is failing"*, and a semester whose autograding fails gets its own
*"Scheduled release (autograde &lt;semester&gt;) is failing"* - so one stuck semester neither hides
the others nor delays them.

Three further issues watch the schedule being *kept* rather than a run's exit code, and also close
themselves. What opens each one is in
[07](../07-schedule-releases.md#what-drives-the-scheduler):

- *"Scheduled release: driver health"*, in your `.github`, cc `course-admin`.
- *"Scheduled release: late delivery"*, in the semester's private `semester-config`, cc that
  semester's `instructors`.
- *"GitHub API budget is running low"*, in your `.github`.
