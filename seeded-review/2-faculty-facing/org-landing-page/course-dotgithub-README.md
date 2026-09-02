<!-- SYSTEM-OWNED - do not edit, edits here are overwritten on the next refresh. -->

# Deep Learning (Demo) - course control panel

This is the **`.github` repo** for the `hertie-dsl-demo-course-e1234` course org - the primary control panel faculty & instructors use
to run and configure the course.

## Run an action

Open the **[Actions tab](https://github.com/hertie-dsl-demo-course-e1234/.github/actions)**, pick a workflow, and click **Run workflow**. Workflows only show if you have write access - i.e. you're either (1) in this org's `course-admin` team (declared here, course-wide), or (2) in a cohort's `instructors-<tag>` team (declared in that cohort's own `classroom-config/people.yml` then back-propagated). The full, annotated list of actions is on the **[org home page](https://github.com/hertie-dsl-demo-course-e1234)**.

## Typical flow

1. **New materials repo** / **New assignment** - scaffold your content repos, then fill them in.
2. Create an empty **cohort org** for the year, add the bot as an Owner, then run **Bootstrap cohort**.
3. Each session: **Release materials** / **Release assignment** - or pre-schedule them in `schedule.yml` (recommended).
4. Grading: **Grade assignment** -> **Sync gradebooks** -> **Render grades** -> **Distribute grades**.

## What's in here

- `.github/workflows/` - the workflows. SYSTEM-OWNED: do not edit or delete them.
- `dsl-course.yml` - this course's identity (name/code) and the registry of `course_admins`, who persist across years. INSTRUCTOR-OWNED. (Per-cohort instructors/TAs and the schedule are declared in the cohort org - not here).
- `profile/README.md` - the public org landing page (an auto-generated repo index). SYSTEM-OWNED: do not edit it.

Built and kept in sync by the [DSL teaching toolkit](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit).
