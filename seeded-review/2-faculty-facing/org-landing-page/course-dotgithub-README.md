# Deep Learning (Demo) - course control panel

This is the **`.github` repo** for the `hertie-dsl-demo-course-e1234` course org - the primary control panel faculty & instructors use to
run and configure the course.

## Run an action

Open the **[Actions tab](https://github.com/hertie-dsl-demo-course-e1234/.github/actions)**, pick a workflow, and click **Run workflow**. Workflow buttons only show if you have write access - i.e. you're either (1) in this org's `course-admin` team (declared here, course-wide), or (2) in a cohort's `instructors-<tag>` team (declared in that cohort's own `classroom-config/people.yml` then back-propagated). The full, annotated list of actions is on the **[org home page](https://github.com/hertie-dsl-demo-course-e1234)**.

## Typical flow

1. **New materials repo** / **New assignment** - scaffold your content repos, then fill them in.
2. Create an empty **cohort org** for the year, add the bot as an Owner, then run **Bootstrap cohort**.
3. Each session: **Release materials** / **Release assignment** (or pre-schedule releases using the `schedule.yml` (recommended))
4. Grading: **Grade assignment** -> **Sync gradebooks** -> **Render grades** -> **Distribute grades**.

## What's in here

- `.github/workflows/` - the workflows, do not delete or edit directly; system owned.
- `dsl-course.yml` - this course's identity (name/code) and registry of persistent-across-years `course_admins`. (Per-cohort instructors/TAs and the schedule are all declared in the cohort org - not here).
- `profile/README.md` - the public org landing page (auto-generated repo index), do not delete or edit directly; system owned.

Built and kept in sync by the [DSL teaching toolkit](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit).
