# New cohort org (once per year)

Stand up the per-year, student-facing org: 
- onboarding, 
- the student roster, 
- released materials, 
- the cohort website, 
- and the schedule that runs the term.

Once each year; the [course org](01-new-course-org.md) it hangs off is permanent.

## Prerequisites

- **(Recommended)**: You're in the course org's `course-admin` team.
- Being in a prior cohort's `instructors-<tag>` team also works. 

## Steps

Live example of every file below: [`example-course/cohort-org/`](../example-course/cohort-org).

1. **Create the cohort org** in the [web UI](https://github.com/account/organizations/new?plan=free&ref_cta=Create%2520a%2520free%2520organization&ref_loc=cards&ref_page=%2Forganizations%2Fplan), 
    - Named **`hertie-<course-slug>-<termtag>`**, termtag `fYYYY`/`sYYYY` - lowercase-kebab (e.g. `hertie-dsl-demo-f2026`). 
      - The `fYYYY`/`sYYYY` tag is necessary; it drives the semester label ("Fall 2026") and which year's `assignment-*` templates the site lists.
    - Select a business/institutional account, and enter `hertie-data-science-lab` into the text box.

2. **Invite `hertie-dsl-bot` as Owner** (Org → People → Invite → role *Owner*).

3. **Run [Bootstrap cohort](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/bootstrap-cohort.yml)**
    - From the **course** org's `.github` Actions tab: `Bootstrap cohort`.
    - `cohort_org` = select the newly created `hertie-<course-slug>-<termtag>`. 
    - This seeds: 
      - **`welcome`** repo (**public** - it is the front door students reach before they are org members) - for student onboarding via `join course` issue tickets.
      - its **`README.md`**, telling them how to join - public like the rest of the repo, yours to reword, and never overwritten
      - **`classroom-config`** repo (hidden-from-students) - containing empty templates for `students.csv`, `teams.csv`, `schedule.yml`, `people.yml`, `grades/`
      - **`students` + `auditors` teams** (empty) - do not edit directly these, these will be populated by the workflow, 
      - **`course-admin` team** for this cohort
      - **`hertie-dsl-demo-f2026.github.io`** auto-deployed website - what it shows, and what you must not hand-edit: [11](11-configure-cohort-site.md)
    - It also **registers the cohort** in the course org's `.github/cohort-courses-pages.yml`. That file is the registry every cohort dropdown reads, so an unregistered cohort is invisible to every workflow; a registered org that is later deleted is pruned from it automatically by the nightly refresh.

> NB: Steps 4, 5 & 6 are covered in full detail in [07-schedule-releases.md](07-schedule-releases.md), [06-enrol-students-to-cohort.md](06-enrol-students-to-cohort.md), & [05-manage-teaching-team.md](05-manage-teaching-team.md).

---

4. **Fill in `classroom-config/schedule.yml` for the whole term** (edit locally or in the web UI → commit to `main`).
    - Full guide for doing so covered in [07-schedule-releases.md](07-schedule-releases.md)
    - Full schema for the schedule [here](DEPLOYMENT-CHECKLIST.md#scheduleyml)


5. *(optional)* **Declare this cohort's instructors/TAs** in `classroom-config/people.yml`.
    - This grants them push on this cohort and on this year's course content repos, and supplies the cohort site's cards.

   ```yaml
   people:
     instructors:
       - github_handle: "janedoe"
     teaching_assistants:
       - github_handle: "anOther-user"
         start: "2026-09-01"     # optional - omit for "active immediately"
         end: "2027-01-31"       # optional - omit for "indefinite"
   ```

    - `github_handle` grants access; a named entry with no `github_handle` is also valid - it is display-only (a site card, no access granted). 
    - The optional `start`/`end` dates **bound when the access is live**: it is granted from `start` and revoked after `end`, automatically - this is how you hand a guest lecturer or a fixed-term TA push access for one term. 
      - Course-wide admins are declared at the **course** level instead (course org → `.github` → `dsl-course.yml` → `course_admins`), not here. 
      - Full guide, including removing people   and how quickly changes land: [05 Manage the teaching team](05-manage-teaching-team.md).

6. **Load the student roster.** 
  - Fill `classroom-config/students.csv` (seeded header-only) with registrar data (`hertie_email, name`)
  - Leave `github_handle, github_id` blank - onboarding fills them). 
  - Add `role: auditor` for anyone who should get the released materials but no assignments and no grades. 
  - The seeded `students.csv.sample` in each newly bootstrapped shows a filled row of each kind, and that repo's `README.md` documents every column.
  - Full details found in [06-enrol-students-to-cohort.md](06-enrol-students-to-cohort.md)

## Next

- [Manage the teaching team](05-manage-teaching-team.md) - the full version of step 5, incl.
  fixed-term access and how to revoke it.
- [Enrol students](06-enrol-students-to-cohort.md).
- [Schedule releases](07-schedule-releases.md) - the full guide to the plan you started in step 4.
- [Release ad hoc](08-release-materials-to-cohort.md), if you want something out before the
  schedule says so.
- [Configure the cohort website](11-configure-cohort-site.md) - the site step 3 just seeded.

---
**Demo:** cohort [`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026), bootstrapped from
[`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/bootstrap-cohort.yml).
