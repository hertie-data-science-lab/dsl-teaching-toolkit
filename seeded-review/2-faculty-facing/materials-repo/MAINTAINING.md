# Maintaining `course-materials-f2026`

Reference for faculty & instructors on how to populate and operate this materials **source** repo. This file is **not** released to students. Keep student-facing wording in `README.md` and operational notes here.

## What to edit vs leave alone

| You edit / add | Visible to students? | Notes |
| --- | --- | --- |
| `lectures/`, `lab/` `readings/`, `other/` | Yes, when you release that session | The released files are copied into the cohort `materials` repo (or another path you provide). |
| root files - `SYLLABUS.md`, `README.md`, or any name you use | Yes, when you name the file as the release path | A root file is released like any other path: type `SYLLABUS.pdf` (or whatever the fule is called) as the `course_source_path`. |
| `MAINTAINING.md` (this file) | No | Your reference; never released. Leave it in the repo. |
| `.github/workflows/` (the Release workflows) | No | **Infrastructure - do not edit or delete.** These run-from-repo workflows are what make releasing work; **Refresh actions** re-seeds them. |

Rule of thumb: edit the content folders and the two root files; leave `MAINTAINING.md` and `.github/workflows/` alone.

## Structure

Any top-level directory containing at least one ordinal-prefixed subdirectory (`01_`, `02_`, `03_`, ...) is a releasable section:

- `lectures/01_session-1/` - one folder per session's lecture files
- `labs/01_session-1/` - one folder per session's lab (feel free to delete the `labs/` folder if your course has none)
- `readings/01_session-1/` - one folder per session's readings. Just drop the readings in and every file is listed and linked for enrolled students automatically. An additional `READINGS.md` (or `.txt`/`.bib`) is OPTIONAL formatting/prose; it is published publicly, while the files stay behind the enrolled-student gate (unless a public site toggles on `actual-readings`)
- root files - your syllabus under any name (`SYLLABUS.md`, `SYLLABUS.pdf`, ...) and `README.md`: released by naming the file as the release path (the runner is case sensitive)

Add more sessions by creating `lectures/02_session-2/`, `readings/02_session-2/`, ... (only the ordinal prefix matters - name the rest whatever you like), or add a whole new section (e.g. `datasets/01_intro/`) - then if you are manually releasing materials run **Refresh actions** so the session dropdown and Release button's section toggles pick it up (unncessary if you are releasing materials using the `schedule.yml`).

## Available actions

The course org's [`.github` Actions tab](https://github.com/hertie-dsl-demo-course-e1234/.github/actions) hosts the buttons that operate this course:

| Action | What it does |
| --- | --- |
| **Release materials** | Copy any path (session folders, root files, etc) into a cohort's `materials` repo by default (or name a specific target path). |
| **Release assignment** | Freeze an assignment template, then generate one private repo per student/group. |
| **New materials repo** | Scaffold a correctly structured materials repo; release functionality comes boostrapped. |
| **New assignment** | Scaffold an assignment template (starter + hidden autograder) ; release functionality comes boostrapped. |
| **Refresh actions** | Re-seed the run-from-repo buttons and repopulate dropdowns after you add sessions/sections. |
| **Check cohort setup** | Read-only per-cohort checklist of what's configured. |

(**Release materials** and **Release assignment** also appear in this repo's own Actions tab.)

## Public course website (optional)

**DEFFERRED:**


The **Publish course website** action can share this repo's materials on a public open-courseware site. Lecture files are always hosted; for readings you choose `reading-list` (text/citation files are shown as a list - keep copyrighted PDFs out of the list by leaving them as non-text files) or `actual-readings` (every reading file is hosted and downloadable - you carry the copyright responsibility).
