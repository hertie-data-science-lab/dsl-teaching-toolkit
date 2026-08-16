# Maintaining `course-materials-f2026` (faculty & instructors)

Reference for faculty & instructors on how to populate and operate this materials **source** repo. This file is **not** released to students - Release materials only copies session folders, the syllabus, and (when toggled) `README.md`. Keep student-facing wording in `README.md` and operational notes here.

## What to edit vs leave alone

| You edit / add | Visible to students? | Notes |
| --- | --- | --- |
| `lectures/`, `readings/` (and any section folders) session content | Yes, when you Release that session | The released files are copied into the cohort `materials` repo. |
| `SYLLABUS.md`, `README.md` (root) | Only if you toggle them on at release | Write `README.md` for students; it replaces the placeholder. |
| `MAINTAINING.md` (this file) | No | Your reference; never released. Leave it in the repo. |
| `.github/workflows/` (the Release buttons) | No | **Infrastructure - do not edit or delete.** These run-from-repo buttons are what make releasing work; **Refresh actions** re-seeds them. |

Rule of thumb: edit the content folders and the two root files; leave `MAINTAINING.md` and `.github/workflows/` alone.

## Structure

Any top-level directory containing at least one ordinal-prefixed subdirectory (`01_`, `02_`, `03_`, ...) is a releasable section - no config to declare it:

- `lectures/01_session-1/` - one folder per session's lecture files
- `readings/01_session-1/` - one folder per session's readings
- `labs/01_session-1/` - one folder per session's lab (delete the `labs/` folder if your course has none)
- `*syllabus*`, `README.md` (root) - released via the syllabus / README toggles

Add more sessions by creating `lectures/02_session-2/`, `readings/02_session-2/`, ... (only the ordinal prefix matters - name the rest whatever you like), or add a whole new section (e.g. `datasets/01_intro/`) - then run **Refresh actions** so the session dropdown and Release button's section toggles pick it up.

## Available actions

The course org's [`.github` Actions tab](https://github.com/hertie-dsl-demo-course-e1234/.github/actions) hosts the buttons that operate this course:

| Action | What it does |
| --- | --- |
| **Release materials** | Copy session folders (+ optional syllabus/README) into a cohort's `materials` repo. |
| **Release assignment** | Freeze an assignment template, then generate one private repo per student. |
| **New materials repo** | Scaffold another structured materials repo. |
| **New assignment** | Scaffold an assignment template (starter + hidden autograder). |
| **Refresh actions** | Re-seed the run-from-repo buttons and repopulate dropdowns after you add sessions/sections. |
| **Check cohort setup** | Read-only per-cohort checklist of what's configured. |

(**Release materials** and **Release assignment** also appear in this repo's own Actions tab.)

## Public course website (optional)

The **Publish course website** action can share this repo's materials on a public open-courseware site. Lecture files are always hosted; for readings you choose `reading-list` (text/citation files are shown as a list - keep copyrighted PDFs out of the list by leaving them as non-text files) or `actual-readings` (every reading file is hosted and downloadable - you carry the copyright responsibility).
