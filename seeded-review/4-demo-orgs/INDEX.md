# Demo org content snapshot

Editable text snapshot of the DEMO orgs' content (course org `hertie-dsl-demo-course-e1234`, cohort orgs `hertie-dsl-demo-f2026` and `hertie-dsl-demo-f2025`), pulled read-only from GitHub. Every file carries a provenance comment on line 1 (`source: https://github.com/<org>/<repo>/blob/<branch>/<path> @ <commit-sha7>`, HTML-comment style for `.md`/`.qmd`/`.Rmd`/`.html`, `#`-comment style for `.yml`/`.yaml`/`.csv`/`.txt`/`.py`/`.R` — for `.csv` files that comment line is line 1 of the file, ahead of the real CSV header on line 2. Edits made here are not tracked anywhere: this is instructor-owned demo content mirrored from the live demo repos, not generator output, so a hand-edit here is only useful as a draft to copy back by hand into the demo repo named in the file's provenance line.


## hertie-dsl-demo-course-e1234

| Repo | Visibility | Files included | Files excluded (why) |
|---|---|---|---|
| .github | PUBLIC | 22 | 1 extension not in scope (binary/data/notebook/etc.); 1 contains 1 email-like / 0 handle-like token(s) |
| assignment-1-f2026 | PUBLIC | 4 | 1 not present on solution branch |
| assignment-2-f2026 | PUBLIC | 3 | 1 extension not in scope (binary/data/notebook/etc.); 1 not present on solution branch |
| assignment-3-project-f2026 | PUBLIC | 7 | 1 extension not in scope (binary/data/notebook/etc.); 1 not present on solution branch |
| course-materials-f2025 | PUBLIC | 12 | 29 over 200KB; 5 extension not in scope (binary/data/notebook/etc.); 1 contains 1 email-like / 0 handle-like token(s) |
| course-materials-f2026 | PUBLIC | 15 | 29 over 200KB; 7 extension not in scope (binary/data/notebook/etc.); 1 contains 1 email-like / 0 handle-like token(s); 1 html not under _layouts/_includes of a site repo |
| hertie-dsl-demo-course-e1234.github.io | PUBLIC | 58 | 17 over 200KB; 15 extension not in scope (binary/data/notebook/etc.); 1 blocked filename (people.yml); 1 html not under _layouts/_includes of a site repo |
| lecture-code-f2026 | PUBLIC | 3 | 4 code file outside an assignment template's main (starter files only); 1 extension not in scope (binary/data/notebook/etc.) |

## hertie-dsl-demo-f2026

| Repo | Visibility | Files included | Files excluded (why) |
|---|---|---|---|
| .github | PUBLIC | 3 | - |
| assignment-1 | PUBLIC | 2 | 1 no solution branch |
| assignment-1-<handle> | PUBLIC | 0 | whole repo skipped - skip-privacy:per-student submission repo (assignment-slug-handle) |
| assignment-2 | PRIVATE | 1 | 1 extension not in scope (binary/data/notebook/etc.); 1 no solution branch |
| assignment-2-<handle> | PRIVATE | 0 | whole repo skipped - skip-privacy:per-student submission repo (assignment-slug-handle) |
| assignment-3-project | PUBLIC | 5 | 1 extension not in scope (binary/data/notebook/etc.); 1 no solution branch |
| assignment-3-project-team-alpha | PUBLIC | 4 | 1 extension not in scope (binary/data/notebook/etc.); 1 code file outside an assignment template's main (starter files only) |
| assignment-3-project-team-beta | PUBLIC | 4 | 1 extension not in scope (binary/data/notebook/etc.); 1 code file outside an assignment template's main (starter files only) |
| assignment-3-project-team-gamma | PUBLIC | 4 | 1 extension not in scope (binary/data/notebook/etc.); 1 code file outside an assignment template's main (starter files only) |
| classroom-config | PUBLIC | 9 | 3 under blocked dir grading_sheets/; 2 under blocked dir autograde/; 2 blocked filename (people.yml); 2 blocked filename (students.csv); 2 blocked filename (teams.csv); 1 blocked filename (cohort-gradebook.csv); 1 under blocked dir gradebook/; 1 under blocked dir snapshots/; 1 extension not in scope (binary/data/notebook/etc.) |
| grades-<handle> | PRIVATE | 0 | whole repo skipped - skip-privacy:grades-<handle> repo |
| hertie-dsl-demo-f2026.github.io | PUBLIC | 72 | 14 extension not in scope (binary/data/notebook/etc.); 8 over 200KB; 1 blocked filename (people.yml); 1 html not under _layouts/_includes of a site repo |
| materials | PUBLIC | 8 | 17 over 200KB; 6 extension not in scope (binary/data/notebook/etc.); 1 html not under _layouts/_includes of a site repo |
| welcome | PUBLIC | 6 | - |

## hertie-dsl-demo-f2025

| Repo | Visibility | Files included | Files excluded (why) |
|---|---|---|---|
| .github | PUBLIC | 3 | - |
| assignment-1 | PUBLIC | 2 | 1 no solution branch |
| assignment-2 | PUBLIC | 0 | 1 extension not in scope (binary/data/notebook/etc.); 1 no solution branch |
| classroom-config | PUBLIC | 9 | 3 under blocked dir grades/; 2 under blocked dir grading_sheets/; 2 blocked filename (people.yml); 2 blocked filename (students.csv); 2 blocked filename (teams.csv) |
| hertie-dsl-demo-f2025.github.io | PUBLIC | 65 | 15 extension not in scope (binary/data/notebook/etc.); 1 blocked filename (people.yml) |
| materials | PUBLIC | 8 | 28 over 200KB; 4 extension not in scope (binary/data/notebook/etc.) |
| welcome | PUBLIC | 6 | - |

**Totals:** 29 repos surveyed, 335 files included, 255 files excluded.
