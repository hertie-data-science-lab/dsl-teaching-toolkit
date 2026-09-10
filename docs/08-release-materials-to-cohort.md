# Manually release materials to a cohort

Deploy any path - a session folder, a dataset, a syllabus file, a code subpackage - from the staging course-org repo into a student-facing cohort repo.

> NB: this is the manual ad hoc alternative to [pre-scheduling & automating](07-schedule-releases.md) the term's releases.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md).
- A course [materials repo](02-add-materials-to-course.md) with the sessions you want to release.
- A bootstrapped [cohort org](04-new-cohort-org.md).

## The schedule automatically handles releases in advance (recommended)

A `deploy` entry in the cohort's `schedule.yml` releases exactly what the workflow below does, at the datetime you give it: [Schedule releases](07-schedule-releases.md). One setup cost, and the entry appears on the deployed `<course>.github.io` site so students see the plan in advance.

## Release materials via manual dispatch

The `release materials` workflow can be found in the course org's: 
  1. `.github` → **Actions** tab → **Release materials** - e.g. [this demo repo](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/release-materials.yml) 
  2. within any bootstrapped materials repo (i.e. any repo created using the `new materials repo` workflow) → **Actions** tab → **Release materials** - e.g. [this demo repo](https://github.com/hertie-dsl-demo-course-e1234/course-materials-f2026/actions)

The workflow's inputs are **the same fields as a `schedule.yml` `deploy` entry** - what you type here is exactly what you would have written in the schedule:

| # | Input | Required | Default | Meaning |
|---|---|---|---|---|
| 1 | `course_source_repo` | yes | *(the repo you run it from; centrally, the latest dated repo)* | repo to release from in the COURSE org |
| 2 | `course_source_path` | yes | - | folder or file to copy - or a **comma-separated list** |
| 3 | `cohort_org` | yes | *(latest cohort)* | target cohort org (dropdown) |
| 4 | `cohort_dest_repo` | yes | - | repo in the cohort org (created if missing, private, `students` + `auditors` read) |
| 5 | `cohort_dest_path` | no | blank → mirrors `course_source_path` (recreates same structure) | where to put it |

### [How to specify release paths](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/release-materials.yml)

- **Release one file**: `course_source_path` = `lectures/02_intro` → lands at `materials/lectures/02_intro`.
- **Release multiple specific files** - use a comma-separated list: `course_source_path` = `lectures/02_intro, readings/02_intro, labs/02_intro`  (each path mirrors itself into `cohort_dest_repo`).
- **Release a whole dir**: `course_source_path` = `lectures/` 
- Root files are just paths too: `course_source_path` = `SYLLABUS.md` 
- **Everything in one press:** `course_source_path` = `/` (or `.`) releases the whole repo, minus the faculty side of it: 
  - `.git` (copying it would repoint the cohort repo at the course repo), 
  - `.github` (the Release workflows and their token wiring) 
  - and `MAINTAINING.md` (your operating notes - the scaffold marks it never released). 

Re-releasing is safe - copies are additive and idempotent.

### Phased code release

The same workflow releases **code**, because code is just another path. Keep a growing package in a course-org repo (e.g. `lecture-code-f2026`) and disclose it topic by topic as you teach: 
- `course_source_path` = `mlpkg/simulation` (a subpackage folder) or `mlpkg/train/warmup.py` (a single module). 
- Copies are additive, so each release extends what students already have 
- release the package base early (e.g. `mlpkg/core`) so partial releases still import. The [example schedule](../example-course/cohort-org/schedule.yml) shows the scheduled version of the same pattern (weeks 1, 3 and 5 each unlock an `mlpkg` subpackage).

## Fixing something you have already released

Two routes, and both stick.

**In the course org**, then release again - the right one for anything next year's cohort
should also get. A release overwrites the file at that path, so the correction lands; a copy
is only additive in that it never *deletes* anything.

**In the cohort's copy**, straight into the released repo - the right one when a lab is
broken during class. Instructors have push there, and the edit survives: a release lands on
the repo's `upstream` branch and is **merged** into the branch students read, so the two
changes are combined rather than one overwriting the other. Carry it back to the course org
afterwards, or next year starts from the uncorrected version (below).

If the same lines changed on both sides, the merge cannot be made automatically. The release
then leaves the branch students read exactly as it was and opens **one pull request**,
`upstream` into that branch, with the `instructors` team asked to review. Nothing is lost -
the released version is on `upstream` - and nothing has moved for students until somebody
decides. Resolve it and **merge**, keeping the cohort's version, the released one, or a mix.
Further releases keep adding to `upstream` and re-use that same pull request; closing it
unresolved does not settle anything, because the conflict is still there and the next
release opens the question again.

`upstream` is the toolkit's branch. Do not work on it and do not make it the default: the
default branch is what students read, what the website reads, and what a release merges
into.

## Carrying cohort edits back

A fix typed into the cohort's copy is not in the course org, so it is not in next term's.
**Propagate cohort edits** (course org → Actions) copies each released path back from the
cohort repo over its source in the course org, on a branch named for the cohort, and opens
one pull request per source repo for faculty to merge, cherry-pick or close. Run it with
`dry_run` first to see the pairs.

What it does and does not carry:

- only paths that have actually been released - it walks the cohort's `deploy:` entries;
- **deletions are not propagated**: a file you removed from the cohort's copy stays in the
  course org, and the pull request says so;
- a cohort repo that is **behind its latest release** - one with the conflict pull request
  above still open - is skipped until that pull request is merged, because its copy is
  missing what the course org has already released; the pull request names what was left;
- the branch is regenerated on every run, so re-running after more edits refreshes the same
  pull request rather than stacking on it.

Closing a cohort runs it first, so the term's corrections are offered back before the repos
are frozen.

## Students propose fixes by pull request

Students read the materials repo and cannot push to it. They can fork it and open a pull
request - which is how they report a typo in a lab, and how both of the courses this toolkit
grew out of have always worked. A pull request lands on the branch students read like any
other, so a merged one is live immediately and survives the next release.

The invitation is in the cohort's home page and in `welcome`. It reaches only cohorts
bootstrapped after it shipped: both files are instructor-owned and seeded once, so an
existing cohort needs the paragraph pasted in by hand.

## Withholding files with `.releaseignore`

Materials repos are scaffolded with a `.releaseignore` whose lines are all commented out -
uncomment or add to it.

Its syntax is **exactly `.gitignore`'s** - patterns, `**`, character classes, `!` to
re-include, `/` to anchor or to mean a directory, `#` comments.

It applies to every copy out of the repo it sits in: the cohort release, the public course
site, and the assignment handout.

What that means in practice:

- **Nested.** A `.releaseignore` in a subfolder applies to that subfolder, and overrides the
  one above it. Patterns anchor to the file's own directory.
- **Silent.** Withheld files simply do not appear. The run stays green and says nothing.
- **Not retroactive.** Adding a pattern stops *future* copies; it does not delete a file you
  already released. Remove that from the cohort repo by hand.
- **Naming a withheld path is refused.** Asking to release a path a `.releaseignore` covers
  copies nothing and warns on the run summary - the same way `git add` refuses an ignored
  file. `--validate` on `schedule.yml` flags it too, so a scheduled release says so when you
  commit it rather than months later when it fires.

Two things differ from `.gitignore`, both deliberate:

- **The file itself is never released.**
- **It covers each repo it lives in, not the whole course.** A pattern in
  `course-materials-f2026` does nothing for `assignment-1-f2026`; put one in each repo you
  want filtered. For an assignment, patterns on the default branch filter the starter
  students receive, and patterns on the `solution` branch filter the model answer.

## The unwritten root stubs are withheld until you write them

Two root files are withheld while they are still the scaffold's placeholder: `README.md` and
`SYLLABUS.md`. Releasing the repo root (`/`), or naming either outright, would otherwise
publish faculty instructions as the students' course overview, and an empty table as their
syllabus - which the site then pins on the cohort home page.

So while the file is untouched the release **skips it and says so**. Everything else in the
release still ships and the run stays green; the withheld file appears as a warning on the
run summary. Rewrite it for students and release again.

Only the ROOT file is ever withheld - a `README.md` inside a session folder is your own
writing about that session and always ships.

## Live updates to the deployed `<course>.github.io` site

Released materials appear on the site automatically: a release triggers **Sync site**, as
does a push to `classroom-config/schedule.yml` or `people.yml`, and there is a daily sync
besides. Run [Sync site](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/sync-site.yml)
by hand only when you don't want to wait - e.g. after editing a file inside an already-released repo.

> What the site shows, what redeploys it, and which files it overwrites:
> [11 Configure the cohort website](11-configure-cohort-site.md).

## Next

- [Add an assignment](03-add-assignment-to-course.md), then [release it](09-release-assignment-to-cohort.md).
- [Schedule releases](07-schedule-releases.md) - the same fields, fired automatically.

---
**Demo:** released into [`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026); site at
`hertie-dsl-demo-f2026.github.io`.
