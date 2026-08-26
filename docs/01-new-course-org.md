# New course org (one-time setup)

Create the persistent org for a course: 
- Its control panel (`.github`, holding every action
workflow), 
- Its teams,
- Later teaching materials and assignment templates are added here

This is setup once per course - it serves every future cohort. Per-semester setup of the student-facing org is [New cohort org](04-new-cohort-org.md).

## Prerequisites

- You are in the `faculty`, `instructors` or `admin` team of
  [`hertie-data-science-lab`](https://github.com/orgs/hertie-data-science-lab/teams) - this gates
  running the *Bootstrap Course Org* workflow.

## Steps

1. **Create the org**: 
   - select the free plan **[here](https://github.com/account/organizations/new?plan=free&ref_cta=Create%2520a%2520free%2520organization&ref_loc=cards&ref_page=%2Forganizations%2Fplan)** → *Create a new organization*. 
   - Name it **`hertie-<course-slug>-<code>`** - lowercase-kebab, no year (e.g. `hertie-dsl-demo-course-e1234`).
   - Select a buisness/institutional account, and enter `hertie-data-science-lab` into the text box.

2. **Invite `hertie-dsl-bot` as Owner**: 
   - `https://github.com/orgs/<your-org>/people` → *Invite member* → `hertie-dsl-bot` 
   - Select role: **Owner**.

   > ⚠️ **The bot must accept the invite before you can bootstrap.** Ask the DSL team (h.baker) to accept it - until they do, the *Bootstrap Course Org* run fails.

3. **Run [Bootstrap Course Org](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/actions/workflows/bootstrap-org.yml)**
   - from the central DSL's [`dsl-teaching-toolkit` repo](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/actions) 
   - Go to the `Actions` tab → select `Boostrap Course Org` 
   - *Run workflow* with the following:

   | Input | Value | Notes |
   |-------|-------|-------|
   | `org` | the org you just made | e.g. `hertie-dsl-demo-course-e1234` |
   | `org_name` | display name | e.g. `DSL Demo Course` |
   | `course_code` | short code | e.g. `E1234` |
   | `set_secret` | `true` (default) | propagates `DSL_BOT_TOKEN` - **don't set the secret by hand** |
   | `admin` | *your handle* | adds you to `course-admin` so you can run the course workflows |

   > This action is safe to re-run in case of need. 
   
   This seeds the `.github` repo with every workflow you'll need ([actions reference](reference/actions-reference.md)), the `course-admin` team, and  `.github/dsl-course.yml` (the course's identity card).

4. **Add any other admins.** 
   - Edit `people.course_admins` in `.github/dsl-course.yml` and commit to `main` 
   - **Sync membership** runs on the push automatically (no need for additional input).

   > Each admin handle gets an org invite that stays `pending` until that person accepts,and GitHub's member list only shows accepted members. Check *People → Pending invitations* if someone looks missing.

   NB: TAs and co-instructors are **not** granted access here; each cohort declares its own in `classroom-config/people.yml` when you [bootstrap that cohort](04-new-cohort-org.md).

## Next

- [Add materials](02-add-materials-to-course.md) and [Add assignment](03-add-assignment-to-course.md) to the course org.
- When the year starts: [New cohort org](04-new-cohort-org.md).

---
**Demo:** course org [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) ·
control panel [`.github` Actions](https://github.com/hertie-dsl-demo-course-e1234/.github/actions).
