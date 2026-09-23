# Manually release an assignment to a cohort

Hand out one **private repo per student** from a course org assignment template.

> NB: this is the manual ad hoc alternative to [pre-scheduling & automating](07-schedule-releases.md) the term's assignment releases.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md).
- An [assignment template](03-add-assignment-to-course.md) with the brief + starter on `main` (staged in the course org).
- A bootstrapped [cohort org](04-new-cohort-org.md) with [students onboarded](06-enrol-students-to-cohort.md) - one repo is generated per onboarded student/group.

## The schedule automatically handles releases in advance (recommended)

A `handout_datetime:` datetime under `assignments.<slug>` in the cohort's `schedule.yml` hands out the same repos automatically - the assignment's whole lifecycle (handout, due date, grading deadline) sits in one block: [Schedule releases](07-schedule-releases.md). One setup cost, and the entry appears on the deployed `<course>.github.io` site so students see the plan in advance.

> NB: a manual release stays compatible with the schedule: on success the workflow **records the release moment into `schedule.yml`** (`assignments.<slug>.handout_datetime`, write-once - a scheduled value is never touched), so the schedule remains the one record of when every assignment went out - and late onboarders get their repo on the next tick.

## Release assignment via manual dispatch

The `release assignment` workflow can be found in the course org's: 
  1. `.github` → **Actions** tab → **Release assignment** - e.g. [this demo repo](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/release-assignment.yml) 
  2. within any bootstrapped assignment repo (i.e. any repo created using the **New assignment** workflow) → **Actions** tab → **Release assignment** - e.g. [this demo repo](https://github.com/hertie-dsl-demo-course-e1234/assignment-1-f2026/actions)


Pick the `course_source_repo` - the same field a scheduled handout names in `schedule.yml`.
- It freezes a cohort-level copy `<name>` of the chosen template (`cohort_dest_repo` from the schedule entry when one names this repo, else the repo minus its tag)
- then it creates one **private** `<name>-<handle>` repo per onboarded student/group, with that student as
collaborator.

Four boxes, in the order you answer them: `course_source_repo`, `cohort_org`, then
- `include_solution` (**off** by default; also push the template's `solution`
branch into each student repo). Schedulable instead, as `solution_datetime:` on the
assignment - see [07](07-schedule-releases.md#releasing-the-model-solution)
- `dry_run` (**off** by default; list the repos that *would* be created).

It asks nothing about the assignment itself: individual or group is the template's own
`grading_config.yml` - see [Group or individual?](#group-or-individual).

Auditors (`role=auditor`) are skipped. The assignment's brief appears on the cohort site automatically - at hand-out, not before, however you hand out.

### An assignment handed in somewhere else

`submit_via: external` in the template's `grading_config.yml` (Moodle, Kaggle, in class)
creates **nothing**: no cohort template, no repo per student, no receipts issue, no
solution push. The handout still records the moment in `schedule.yml`, publishes the brief
and a **Submit on \<host\>** button (from `submit_url`) on the cohort site, writes the
grading sheet with every student or team in it, and makes sure each student has their
private `grades-<handle>` gradebook.

### An assignment handed into one shared drop box

`submit_via: shared_dropbox_repo` freezes the cohort template as usual - the brief lives there - and
then creates exactly **one** repo, `<slug>-submissions`: private, with every onboarded
student (or every vetted team) on `push`, and a ruleset asked to stop force-pushes and
deletion - though that needs GitHub Team, and every Hertie org is on Free until the
Education upgrade lands, so until then the drop box is left unprotected and the run log
says so. Each unit pushes into its own `<handle>/` or `<team>/` folder and can read
everyone else's.

There is no receipts issue and no model solution push - one repo the whole cohort reads
is not a place for either. The handout re-fires every quarter of an hour
like any other, and a student who onboards later is granted push on the next tick.

### An assignment whose repos are public

`visibility: public` in the template's `grading_config.yml` hands out the same repos
world-readable - portfolio work such as a hackathon. There is then **no receipts issue**
(a hand-in time is a fact about a student, and it does not go where the internet can read
it; the marks were never going here anyway), and the
assignment's page on the cohort site - and the repo's own About line - says the repo is
public before they push anything into it.

No **model solution** is pushed into these repos, whatever `include_solution` or
`solution_datetime:` says - publishing the answers is not something a later run could take
back - so it stays on the template's `solution` branch for the teaching team.

GitHub turns **secret scanning and push protection** on for a public repository itself, so
every one of these repos has both from the moment it is created. If a student pushes
something that looks like a credential, GitHub refuses the push and prints a link; opening
it lets them say why they are pushing it, which unblocks that push. Tell them to rotate the
credential rather than bypass the block.

### An assignment the students may publish themselves

`visibility: student_choice` in the template's `grading_config.yml` hands out the same
**private** repos, but makes the student - or every member of a team - **admin** of their
own, which is the only permission that carries GitHub's visibility switch. There is no
receipts issue (the repo may be public tomorrow).

No model solution is pushed into these repos either, for the same reason: the student may
publish the repo the day after the cutoff, and the answers would go with it.

Until the assignment's **grading cutoff** the scheduler puts any of these repos back to
private on its next tick, and the assignment's page on the cohort site says so. After the
cutoff nothing touches the flag again: the repo is theirs to publish.

One-time setup on the cohort org, by hand (these are web-only settings - no API sets them):
Settings → Member privileges → **Allow members to change repository visibilities** ON,
**Allow members to delete or transfer repositories** OFF. The cohort's *grading_config.yml*
digest issue reports it while either is wrong.

## Group or individual?

The shape is the template's own declaration - `type:` in the `grading_config.yml` on its
`solution` branch, which **New assignment** writes at scaffold time. The schedule carries
only the dates:

```yaml
assignments:
  assignment-4-project:
    handout_datetime: 2026-10-20T14:00
```

Nothing overrides it: the teams, the grading sheet and the Join-team form are all keyed
on that declaration, so a handout free to disagree with it would put a cohort's work in
repos nothing else is looking for. To change the shape, edit `grading_config.yml`.

- `group` = one shared repo per team from `teams.csv` (repo `<slug>-<team>`, every member a collaborator), marked per team in the grading sheet's `teams:` block, with one `adjustment_individual` per member.
- `individual` = one private repo per onboarded, enrolled student (`<slug>-<handle>`), marked in the sheet's `submissions:` block.

## Group assignments: creating the teams

Live example: [`example-course/cohort-org/teams.csv`](../example-course/cohort-org/teams.csv).

Teams are formed in one of two ways - both end up in `classroom-config/teams.csv` (`assignment, team, github_handle`), and **Sync membership** turns each into a GitHub team on push. A team need not exist before the hand-out: the release provisions one shared repo per team that exists, and a scheduled hand-out re-fires every tick, so a team formed on day three gets its repo then.

Which of the two an assignment uses is its own declaration - `team_formation` in the `grading_config.yml` on the template's `solution` branch:

- **`assigned`** - you edit `teams.csv` directly, one row per member. The **Join team** form refuses every request for this assignment and says so.
- **`self_select`** - students open a **Join team** issue in the cohort's `welcome` repo. Team size is capped by that assignment's `max_team_size` (default: the course's `assignment_defaults`, else 5).

**The form** asks three things: which assignment, whether they are **joining an existing team** or **creating a new one**, and the team's name. The choice is explicit because it used to be implied by the name - a name that existed joined, a name that did not created - so one typo opened a second, half-empty team that nobody noticed until the release provisioned it. Creating onto a name that is taken is refused; joining a name that does not exist is refused with the nearest real team named. Its header links each open assignment's page on the cohort site, which lists the teams. It reads all of this out of `classroom-config/assignments.lock.yml`, which the toolkit generates from each assignment's `grading_config.yml` and the cohort's schedule, and nobody edits - change either and the mirror catches up on the next quarter-hourly **Scheduled release** tick, which is also what opens and shuts the window below on time.

**The window** runs from the assignment's `handout_datetime` to its grading pin: `grading_datetime` if the schedule sets one, else the due date - *not* the end of the late window, because a team minted after the snapshot has nothing left to hand in. Before the hand-out the form says formation is not open yet and when it will run; after the pin it says the day it closed. Move either date and the window moves. An assignment with no `handout_datetime` on record has no window at all - but **Release assignment** writes that moment into `schedule.yml` itself, so a hand-fired hand-out opens one too. While it is open, the form's Assignment field is a drop-down of exactly the assignments a student may act on - and it moves with the window: the same tick that opens or shuts one rewrites the form, as does a push to `schedule.yml`.

**The team list is the assignment's page on the cohort site.** `teams.csv` is private, so a student cannot see what to join. While the window is open, that page carries a **Form your team** callout - the cap, the closing day, a link to the form, and a table of the teams that exist with the places each has left (**names and counts only**, because the site is public) - and its schedule row carries the same prompt in one line. The mail, the form's header and the form's refusals all link that page. A push to `teams.csv` fires a site sync, so the table follows each join within minutes. The submission address stays beside it rather than in its place: a team that formed on day one owns its repo already. All of it goes when the window shuts.

**The students are emailed** - the only mail the toolkit sends off a clock rather than off something you did. When the window opens, every enrolled, onboarded student with no team for that assignment gets one plain message, addressed to them by name and naming the assignment as the site does (`Assignment 3: Project`): the cap, the day formation closes, the link to the form and the link to the assignment's page. Anyone still without a team 48 hours before it shuts gets one more, and a student who onboards mid-window is asked on the next tick. Nothing goes out between 23:00 and 07:00 in the cohort's timezone - the message waits for the morning, while the lock, the form and the site still turn at the window's own minute - nothing is said twice (`team-formation/mailed.csv` in `classroom-config` is the record), and a cohort whose course org has no `GRAPH_*` mail secrets is sent nothing and has nothing recorded against it. It does not say whether working alone is allowed - that is yours to tell them.

**Open team formation** (the course org's Actions tab) sends that same message on your own say-so - useful right after you announce the assignment in class, or once a cohort's `GRAPH_*` secrets are finally set. Pick a cohort, and optionally one `schedule.yml` assignment key; left empty it asks about every window open right now, and a key with no open window is an error rather than a quiet no-op. Same message on the same record, so a second press reaches only the students the first could not, and the overnight hold applies to a press exactly as it does to the clock. Dry run first.

**You hear about it too.** While anybody is still without a team, the cohort's *schedule.yml* [digest issue](07-schedule-releases.md#the-digest-issue) carries a line on that assignment - counts only, never a name - and it gets louder as the window runs out, exactly like a source nobody has staged. A window that has stood open for a week with anybody still waiting is at least a warning, so you hear about it well before the last day. It does **not** go away when the window shuts: that is the moment the problem becomes permanent, so the line stays at its loudest rung until you fill the gaps in `teams.csv`, move the closing date to give the cohort longer, or take the entry out of the plan. Standing there it says nothing new - no further comment, no further email - it simply does not report itself as fixed while those students still have no repo to hand into.

Full flow:
[Enrol students → groups](06-enrol-students-to-cohort.md#group-assignments-rolling-basis).

## Deadlines

Set in the **cohort's** `classroom-config/schedule.yml`, keyed by an assignment **slug** you choose - `course_source_repo` names the actual course-org repo (tag included):

```yaml
assignments:
  assignment-1: # this is the name students will see
    course_source_repo: assignment-1-f2026  # required: the course-org repo it hands out from
    due_datetime: 2026-10-13          # the due date students see
    grading_datetime: 2026-10-15      # OPTIONAL, grading-only - the cutoff
```

- **The date students see** (cohort site + the brief's "due" event) is `assignments[slug].due_datetime`
  (23:59 that day). Edit → commit to `main` - **Sync site** fires automatically on the push.
- **The late window** is the template `grading_config.yml`'s `late_window_days` (with
  `late_penalty_per_day`, a percentage of the earned grade per day started). Between the due
  date and the cutoff the grading sheet keeps refreshing and each late push earns a receipt.
  Where neither the assignment nor the course states them, the Hertie standard applies:
  10% per day started, collected for up to 10 days. `late_window_days: 0` accepts nothing
  after the deadline.
- **The cutoff** is `grading_datetime` if set, else the due date plus that late window.
  - At that moment [the scheduler](07-schedule-releases.md#what-drives-the-scheduler) freezes the snapshot and the grading sheet, and autogrades it where the template asks for it (`autograde: true`, off by default).
- **The commit that is considered submitted for grading** is frozen right after the grading deadline passes, into
`classroom-config/snapshots/<slug>.csv`. It is **write-once** - later pushes can't move the
  pin. To deliberately re-freeze (e.g. repos provisioned late), delete the CSV and the next
  scheduled tick rebuilds it.

## Fixing a file after the assignment has gone out

A broken cell, a wrong path, a dataset that moved. The repos are in students' hands
already, so editing the template changes nothing for them - **Patch released assignment**
is what distributes the fix.

1. Commit the correction to the **template's default branch** (`main`) as you normally
   would. This button only distributes what is already there.
2. Course org → `.github` → **Actions** → **Patch released assignment**. Inputs:
   `cohort_org`, `course_source_repo` (the template), `path` (a file, or a folder to push
   whole), `slug` (only when two schedule entries hand out from this one template),
   `overwrite` (default **off**) and `dry_run` (default **on**).
3. Dry run first: it counts the repos it would touch and writes nothing.

What a real run does:

- pushes the file into every submission repo of that assignment as a **new commit** on the
  student's own branch. Never a force-push, so nothing they have committed is lost;
- **skips any file the student has already changed** - unless you tick `overwrite`, which
  is a decision to discard their version of that file. "Changed" means the file no longer
  matches the frozen cohort-side hand-out their repo was generated from;
- patches that frozen hand-out too, so a student who onboards tomorrow is given the
  corrected file rather than the one everybody else was just patched off;
- posts one note on each patched repo's **Submission receipts** issue: *"The teaching team updated
  `starter.ipynb` in this repository on 2026-10-14; pull before you continue. Your own
  commits are untouched."* A second press that changes nothing says nothing.

Text files only - notebooks, scripts, briefs, CSVs. The run log carries counts, never a
repo name.

## Next

- [Grade and return the assignment](10-grade-and-return-assignments.md).

---
**Demo:** per-student repos in [`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026).
