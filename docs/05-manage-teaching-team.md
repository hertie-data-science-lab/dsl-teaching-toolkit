# Manage the teaching team

Give an instructor, TA, faculty assistant or guest lecturer access to a course - permanently or for a fixed window.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md), 
- A bootstrapped [cohort](04-new-cohort-org.md).
- Instructors' **GitHub handles**. That is the only required field; everything else is display.

---

Access is **declared in a config file and reconciled**: 
  1. You edit the file, commit & push
  2. The **Sync membership** action in the **course** org's `.github` materialises the GitHub teams. 

There is no need to edit GitHub team directly. This provides an auditable historical record.

## Two levels

| Role | You want them to… | Declare them in | Level | They get |
|--- |---|---|---|---|
| Faculty, FAs | Administer the **whole course**, every cohort, indefinitely | course org `.github/dsl-course.yml` → `people:` `course_admins` | **course** - once, for all years | `course-admin` (admin) on the course org **and** every cohort org |
| TAs, Guest Lecturers| Push materials/assignments for **one year** and run the release workflows | that cohort's `classroom-config/people.yml` → `instructors` / `teaching_assistants` | **cohort** - per year | cohort org `instructors` team + course org `instructors-<tag>`: push on `.github` and on every course-org repo named `*-<tag>` |

**Prefer the cohort file** for anyone who isn't running the course across multiple years. It is per-year, self-retiring, and it also supplies the deployed site's staff cards with rich display and information.

>Full model - every team and what it reaches: [`access-reference.md`](reference/access-reference.md).

## Add someone

1. **Edit the file.**

   **Cohort org**: → `classroom-config` → `people.yml` (this year's teaching team):

   ```yaml
   people:
     instructors:
       - github_handle: "janedoe"        # required 
         name: "Prof. Jane Doe"          # optional, from here down
         title: "Professor of ..."
         photo: "/_images/pp/jane.jpg"   
         url: "https://.../jane"
     teaching_assistants:
       - github_handle: "henrycgbaker"
   ```

   Or **course org** → `.github` → `dsl-course.yml` (course-wide admin):

   ```yaml
   people:
     course_admins:
       - github_handle: "janedoe"
   ```

2. **Commit to `main`.** 
  - The push dispatches **Sync membership** automatically - nothing to run by hand. 
  - It reconciles fully (adds *and* removes) to match the pushed file.

3. **They accept the org invite.** 
  - Membership shows `pending` in the Teams UI until they do.
  - Once accepted, the workflows appear in their Actions tab afterwards. 

>Enrolling the student roster is a separate process to the above.

## Head photos (optional)

`photo` accepts either form:

1. **A site-relative path** 
  - E.g. `/_images/pp/jane.jpg`. 
  - Commit the image into this cohort's site repo, `<cohort-org>.github.io`, under `_images/pp/`. 
  - This is the **safe default**.
2. **An absolute URL**
  - It has to be a URL for a host that allows hotlinking. 
  - GitHub avatars (`https://github.com/<handle>.png`) always work.

> Institutional profile sites often block off-site requests. E.g. `hertie-school.org` returns **403** to anything not loaded from its own pages.

## Time-box it (`start` / `end`)

Every person entry, in **either** file, takes two optional ISO dates. This is how you give a guest lecturer, a visiting faculty member or a TA access

```yaml
people:
  teaching_assistants:
    - github_handle: "anOther"
      start: "2026-09-01"      # optional - omit for "active immediately"
      end: "2027-01-31"        # optional - omit for "indefinite"
```

>Because every sync is a full reconcile, a lapsed `end` prunes them exactly as a deleted entry would - no manual removal step needed. Leave the entry in the file afterwards: it doubles as the record of who taught what, and re-granting next year is a date edit.

Worked example: [`example-course/cohort-org/people.yml`](../example-course/cohort-org/people.yml).

## Remove someone / end access early

- **Time-boxed:** do nothing, or bring the `end` date forward.
- **Immediately:** delete their entry (or set `end` to yesterday) and push. The dispatch on that push revokes within a minute or two.
- **Do not use the GitHub Teams UI.** A hand-add to `course-admin`, `instructors` or `instructors-<tag>` is reverted by the next sync, and a hand-*removal* of someone still named in the config is re-added. The file is the truth.

## What the access actually reaches

`instructors-<tag>` gets:
1. **push** on the course org's **`.github`** - which is what makes the workflows (Release materials, Release assignment, Refresh actions, Check cohort setup…) visible and runnable for them
2. every course-org repo whose **name ends their associated `-<tag>`** (`course-materials-f2026`, `assignment-1-f2026`, `lecture-code-f2026`).
3. Cohort-side they also get write on `classroom-config` and `welcome`, so they can edit the roster, schedule and team lists.

So a TA on f2026 can `git push` labs into the course org level `course-materials-f2026` ([02](02-add-materials-to-course.md)) and then release them to the cohort org ([08](08-release-materials-to-cohort.md)) themselves.

>The suffix match is the whole rule: a course-org repo **without** the year tag in its name is not covered. Name per-year content repos `<thing>-<tag>`. 
>
>A repo scaffolded today is picked up by the next sync - run **Sync membership** if you want it now.

## Next

- [Enrol students](06-enrol-students-to-cohort.md) - the other half of populating a cohort.
- [Add materials to the course](02-add-materials-to-course.md) - what a new TA usually does first.
- Field-by-field schemas: [DEPLOYMENT-CHECKLIST](DEPLOYMENT-CHECKLIST.md#peopleyml).

---
**Demo:** [`hertie-dsl-demo-f2026/classroom-config/people.yml`](https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/people.yml)
→ [`hertie-dsl-demo-course-e1234` teams](https://github.com/orgs/hertie-dsl-demo-course-e1234/teams).
