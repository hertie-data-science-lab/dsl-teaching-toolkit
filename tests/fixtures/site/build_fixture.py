#!/usr/bin/env python3
"""Write a complete semester site into a directory, from the real renderers.

What the `jekyll-contract` CI job builds (.github/workflows/ci.yml), and what
tests/test_site_templates.py cross-checks the templates' Liquid keys against. Both read
the SAME site, so the offline key check and the real Jekyll build cannot disagree about
what the sync writes.

Generated, never hand-written: the whole site comes out of `dsl_course`'s own functions -
the collection pages and `_data/*.yml` from `site`, and `_config.yml`, `index.md` and the
rest of the seed-once half from `templates/site-seed/`. Only the THEME's stand-ins - its
`default`/`page`/`post` layouts and stylesheet entrypoint, which the offline build does
not fetch - are vendored, under `base/`.

The states it covers are the ones that render DIFFERENTLY, one of each: a released
session, an unreleased one, a lab, a session whose readings are still to come, a
handed-out assignment with a declared `details:`, a pending one, one handed in off GitHub, one handed in off GitHub
that is not out yet, one whose repos are public, one handed into a shared drop box, one
group assignment inside its team-formation window, a dated exam and a TBC
one, a special event, the two term boundaries, the archive row inside its notice window,
an All Materials index nested three directories deep, and - within the released session -
a published file linked to the site's own hosted copy beside an unpublished one linked to
GitHub.

    python3 tests/fixtures/site/build_fixture.py <dest>
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from dsl_course import grades, schedule, schedule_plan, site, site_repo

BERLIN = ZoneInfo("Europe/Berlin")
COURSE_ORG = "hertie-dsl-fixture-course"
SEMESTER_ORG = "hertie-dsl-fixture-f2026"
MATERIALS = "course-materials"
# Handed in off GitHub (`submit_via: external`), so NO repo is created for it: its page and
# its due row must name none, and must not tell the semester to push to `main`. It names a
# `submit_url`, which is what puts the `Submit on <host>` button on both.
EXTERNAL = "assignment-3-f2026"
EXTERNAL_URL = "https://moodle.example.edu/mod/assign/view.php?id=EXAMPLE"
# Portfolio work (`visibility: public`): the same repo per student, world-readable from
# hand-out, and no receipts issue - so its page and its due row have to say so before a
# student pushes anything into it.
PUBLIC = "assignment-4-f2026"
# External AND still pending, which is the pair of states that reaches no reader: the
# brief is embargoed until hand-out, so the page may not yet say where the work goes.
EXTERNAL_PENDING = "assignment-5-f2026"
# The student's own call (`visibility: student_choice`): the same private repo, but the
# student is its admin and may publish it once the grading cutoff has passed. Its page has
# to carry both halves of that, and its due row the one word that says the flag is theirs.
STUDENT_CHOICE = "assignment-6-f2026"
# One drop box for the whole semester (`submit_via: shared_dropbox_repo`): `repo_name` is a REAL repo
# rather than a shape, what is the student's own is a folder inside it, and the name must
# NOT be rewritten to one per reader - which is the one thing open_in.html does to every
# other assignment page.
SHARED = "assignment-7-f2026"
# A group assignment whose teams the students pick themselves (`team_formation:
# self_select`), rendered INSIDE its team-formation window: the brief is out, no repo
# exists yet because no team does, and the page carries the invitation to form one in
# place of the submission-repo button. Dated before the first lecture for the same reason
# 06 and 07 are - see the note beside them.
GROUP_FORMING = "assignment-8-f2026"
# The moment the fixture is rendered "at", so a handout pin is in the past or the future
# by construction rather than by when CI happens to run.
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=BERLIN)
# The plan `_assignment_entry` reads the team-formation window off. Only the group
# assignment above needs one - every other fixture assignment is individual, and answers
# `none` on the spec alone.
FORMING_KEY = "assignment-8"
FORMING_SCHEDULE = schedule.Schedule(
    assignments={
        FORMING_KEY: schedule.AssignmentEntry(
            course_source_repo=GROUP_FORMING,
            # Open at NOW: handed out a month ago, and the grading pin that shuts the
            # window is still two months off.
            handout_datetime=datetime(2026, 8, 31, 9, 0, tzinfo=BERLIN),
            due_datetime=datetime(2026, 11, 24, 23, 59, tzinfo=BERLIN),
            grading_datetime=datetime(2026, 11, 26, 9, 0, tzinfo=BERLIN),
        )
    }
)

# The semester's released tree, as `_repo_tree` would report it. Three directories deep
# under `lectures/01_week-1/`, which is the nesting the All Materials index recurses over
# - the include that once rendered an empty page and took two live sites down.
TREE = (
    "SYLLABUS.md",
    "labs/01_week-1/Lab_Session_1.ipynb",
    "lectures/01_week-1/demo.py",
    "lectures/01_week-1/handouts/extra/further-notes.pdf",
    "lectures/01_week-1/slides.html",
    "lectures/01_week-1/slides_files/figure/plot.svg",
    "lectures/01_week-1/slides.pdf",
    "lectures/02_week-2/slides.pdf",
    "readings/01_week-1/READINGS.md",
    "readings/01_week-1/schmidhuber-1997.pdf",
)

# What this course's `publish.yml` declares, through the real parser: rendered decks and
# nothing else - the shape a Quarto course takes. Session 1 therefore carries BOTH row
# shapes, a published `slides.html` (whose `slides_files/` bundle follows it) beside an
# unpublished `slides.pdf`, which is the pair the templates have to tell apart.
PUBLISH_POLICY = {MATERIALS: (site.parse_patterns("lectures/**/*.html"),)}

READINGS_MD = """# Session 1 readings

## Required

- Hochreiter & Schmidhuber (1997), *Long short-term memory*.

## Optional

- Anything in `schmidhuber-1997.pdf` you find interesting.
"""

ASSIGNMENT_README = """# Predicting rainfall from station data

Fit a model, write up what it does and where it fails.

## Submission

Push to `main`.
"""

PEOPLE = {
    "people": {
        "instructors": [
            {
                "github_handle": "prof-a",
                "name": "Alex Instructor",
                "photo": "/_images/pp/placeholder.jpg",
                "url": "https://example.invalid/alex",
                "title": "Professor of Data Science",
            }
        ],
        "teaching_assistants": [
            {
                "github_handle": "ta-b",
                "name": "Blair Assistant",
                "photo": "/_images/pp/placeholder.jpg",
            }
        ],
    }
}


def _repo_tree(_org: str, repo: str) -> tuple[str, tuple[str, ...]]:
    return "main", TREE if repo == MATERIALS else ()


def _formed_teams(_org: str, _key: str) -> list[tuple[str, list[str]]]:
    """Two teams for the assignment inside its team-formation window - one with room and
    one full, so the built page carries both halves of the Places-left column."""
    return [
        ("team-alpha", ["ada-l", "bo-b"]),
        ("team-bravo", ["cy-c", "di-d", "ed-e", "flo-f"]),
    ]


def _grading_spec(_org: str, repo: str):
    """The assignment's own definition, which names the repo shape a student looks for,
    says whether the work is handed in on GitHub at all, and declares what happens to work
    that arrives late. The fixture's assignments are individual, so the rest of the empty
    file's defaults are exactly right - it is stubbed only because the read would otherwise
    go to GitHub.

    The default carries a late WINDOW and a penalty and the named shapes do not, so the
    generated pages hold both halves of `course.late_rule` - the rule quoted and the
    deadline standing alone - and `external` holds neither. It declares `questions:` for
    the same reason: the total off those maxima is what the page prints as `max_points`,
    and the named shapes declare none, so the fixture holds a page with the line and pages
    without it."""
    if repo in (EXTERNAL, EXTERNAL_PENDING):
        return grades.parse_grading_spec(
            f"submit_via: external\nsubmit_url: {EXTERNAL_URL}\n"
        )
    if repo == PUBLIC:
        return grades.parse_grading_spec("visibility: public\n")
    if repo == STUDENT_CHOICE:
        return grades.parse_grading_spec("visibility: student_choice\n")
    if repo == SHARED:
        return grades.parse_grading_spec("submit_via: shared_dropbox_repo\n")
    if repo == GROUP_FORMING:
        # The cap is declared, which is what `New assignment` stamps into every file it
        # writes - so the page prints it without the course-org read `grades.team_cap`
        # falls back to.
        return grades.parse_grading_spec(
            "type: group\nteam_formation: self_select\nmax_team_size: 4\n"
        )
    return grades.parse_grading_spec(
        "late_window_days: 7\nlate_penalty_per_day: 10%\nquestions:\n  Q1: 15\n  Q2: 10\n"
    )


def _get_file_content(_org: str, _repo: str, path: str) -> str | None:
    if path.endswith("READINGS.md"):
        return READINGS_MD
    if path == "README.md":
        return ASSIGNMENT_README
    return None


def _lectures(hosted: dict) -> dict[str, str]:
    """The `_lectures` collection: the four session states that render differently."""
    released = schedule_plan.PlannedRow(
        when=datetime(2026, 9, 7, 10, 0, tzinfo=BERLIN),
        subtitle="What a neural network is",
        details="Perceptrons, activation functions and the chain rule.",
        readings_planned=True,
    )
    pending_readings = schedule_plan.PlannedRow(
        when=datetime(2026, 9, 14, 10, 0, tzinfo=BERLIN),
        subtitle="Backpropagation",
        readings_planned=True,
    )
    unreleased = schedule_plan.PlannedRow(
        when=datetime(2026, 9, 21, 10, 0, tzinfo=BERLIN),
        dests={f"{MATERIALS}/lectures/03_week-3": None},
        subtitle="Convolutions",
        details="Why weight sharing works.\n\nAnd where it does not.",
        readings_planned=True,
    )
    lab = schedule_plan.PlannedRow(when=datetime(2026, 9, 9, 14, 0, tzinfo=BERLIN))
    return {
        # Released, with a reading list inlined off the released READINGS.md overlay.
        "session-01.md": site._lecture_entry(
            SEMESTER_ORG,
            "1",
            released,
            [
                (MATERIALS, "lectures", "01_week-1"),
                (MATERIALS, "readings", "01_week-1"),
            ],
            hosted=hosted,
        ),
        # Released, but the plan's readings have not landed -> readings_pending.
        "session-02.md": site._lecture_entry(
            SEMESTER_ORG,
            "2",
            pending_readings,
            [(MATERIALS, "lectures", "02_week-2")],
            hosted=hosted,
        ),
        # Nothing shipped -> unreleased, and the row names where it will land.
        "session-03.md": site._lecture_entry(
            SEMESTER_ORG,
            "3",
            unreleased,
            [],
            live_repos=frozenset({MATERIALS}),
            hosted=hosted,
        ),
        "lab-01.md": site._lecture_entry(
            SEMESTER_ORG,
            "1",
            lab,
            [(MATERIALS, "labs", "01_week-1")],
            kind="lab",
            hosted=hosted,
        ),
    }


def _assignments() -> dict[str, str]:
    """Every way an assignment says where the work goes: one handed out (repo link, brief,
    README-derived name), one still pending, one handed in off GitHub, one handed in off
    GitHub but not out yet, one whose repos are public, and one whose repos the students
    may publish themselves.

    The external ones are handed out by their PIN rather than by a frozen semester template:
    that handout creates no repos at all, so `handed_out` never carries their name and
    `site._assignment_entry`'s other half is what publishes the brief."""
    return {
        # The only one the plan NAMES, so the only one that can carry a declared
        # `details:` - which rides both its rows, the due row's copy nested a level in.
        "01-assignment-1.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            "assignment-1-f2026",
            datetime(2026, 10, 20, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 9, 29, 9, 0, tzinfo=BERLIN),
            found=(
                "assignment-1",
                schedule.AssignmentEntry(
                    due_datetime=datetime(2026, 10, 20, 23, 59, tzinfo=BERLIN),
                    course_source_repo="assignment-1-f2026",
                    details="Closed form first, then by gradient descent.",
                    tbc=True,
                ),
            ),
            handed_out=frozenset({"assignment-1"}),
            now=NOW,
        ),
        "02-assignment-2.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            "assignment-2-f2026",
            datetime(2026, 11, 24, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 11, 3, 9, 0, tzinfo=BERLIN),
            now=NOW,
        ),
        "03-assignment-3.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            EXTERNAL,
            datetime(2026, 12, 8, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 9, 30, 9, 0, tzinfo=BERLIN),
            now=NOW,
        ),
        "04-assignment-4.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            PUBLIC,
            datetime(2026, 12, 15, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 9, 29, 9, 0, tzinfo=BERLIN),
            handed_out=frozenset({"assignment-4"}),
            now=NOW,
        ),
        "05-assignment-5.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            EXTERNAL_PENDING,
            datetime(2026, 12, 22, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 11, 10, 9, 0, tzinfo=BERLIN),
            now=NOW,
        ),
        # Handed out BEFORE the first lecture, and so is 07: the home page's Updates box lists
        # the seven newest released items, and the jekyll-contract job greps that box for the
        # first lecture's inline source link. Two more assignments dated after it pushed it
        # to eighth place and the contract failed on a page nothing here had touched.
        "06-assignment-6.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            STUDENT_CHOICE,
            datetime(2027, 1, 12, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 9, 1, 9, 0, tzinfo=BERLIN),
            handed_out=frozenset({"assignment-6"}),
            now=NOW,
        ),
        "07-assignment-7.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            SHARED,
            datetime(2027, 1, 19, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 9, 2, 9, 0, tzinfo=BERLIN),
            handed_out=frozenset({"assignment-7"}),
            now=NOW,
        ),
        # Out, and waiting for its teams: the brief is published, no `repo_url` is, and
        # the `team_join_*` keys and the teams formed so far say what to do about it
        # instead.
        "08-assignment-8.md": site._assignment_entry(
            COURSE_ORG,
            SEMESTER_ORG,
            GROUP_FORMING,
            datetime(2026, 11, 24, 23, 59, tzinfo=BERLIN),
            handout=FORMING_SCHEDULE.assignments[FORMING_KEY].handout_datetime,
            found=(FORMING_KEY, FORMING_SCHEDULE.assignments[FORMING_KEY]),
            now=NOW,
            sched=FORMING_SCHEDULE,
        ),
    }


def _events() -> dict[str, str]:
    """A dated exam, a TBC one, a special event, the two term boundaries, and the archive
    row inside its notice window - which is the only state of it that renders anywhere but
    the schedule table."""
    end = date(2026, 12, 18)
    archived = end + schedule.ARCHIVE_GRACE
    return {
        "01-midterm-exam.md": site._event_row(
            "exam",
            "MidTerm Exam",
            datetime(2026, 11, 2, 9, 0, tzinfo=BERLIN),
            details="Room A1. Two hours, one double-sided sheet of notes.",
        ),
        "02-guest-lecture.md": site._event_row(
            "special_event",
            "Guest lecture: forecasting at scale",
            datetime(2026, 10, 12, 16, 0, tzinfo=BERLIN),
        ),
        "03-resit-exam.md": site._event_row(
            "exam", "Resit Exam", end, tbc=True, dateless=True
        ),
        "term-start.md": site._term_date_entry("Semester starts", date(2026, 9, 7)),
        "term-end.md": site._term_date_entry("Semester ends", end),
        # Inside its notice window and with a sentence, which is the only shape that
        # carries `announce: true` - the one row the Updates box takes off the schedule.
        "semester-archived.md": site._archive_entry(
            schedule.ArchiveRow(
                when=archived,
                details=(
                    "This semester goes read-only on {date}. You keep read access to "
                    "everything."
                ),
            ),
            archived - schedule.ARCHIVE_NOTICE,
        ),
    }


def collections(hosted: dict) -> dict[str, dict[str, str]]:
    """The generated collections, front-matter stamp and all - exactly what
    `_sync_site_repo` writes into the site repo."""
    return {
        "_lectures": _lectures(hosted),
        "_assignments": _assignments(),
        "_events": _events(),
    }


def data_files(hosted: dict) -> dict[str, str]:
    """The generated `_data/*.yml`, keyed by repo-relative path."""
    return {
        "_data/people.yml": site_repo.people_yaml(
            SEMESTER_ORG, PEOPLE, edit_at=f"{SEMESTER_ORG}/classroom-config/people.yml"
        ),
        "_data/nav.yml": site_repo.nav_yaml(semester=True),
        "_data/materials.yml": site._materials_index(
            SEMESTER_ORG,
            [MATERIALS],
            hosted,
            syllabus=site_repo.Link(
                "SYLLABUS.md",
                f"https://github.com/{SEMESTER_ORG}/{MATERIALS}/blob/main/SYLLABUS.md",
            ),
        ),
    }


def generated(
    site_wd: Path | None = None,
) -> dict[str, dict[str, str] | dict[str, dict[str, str]]]:
    """Everything the sync writes, with the gh reads stubbed out and put back.

    Reassigning the module globals rather than passing fakes down: `_repo_tree` is
    memoised and `get_file_content` is imported into `site`'s namespace, so this is the
    seam every caller below actually goes through.

    `site_wd` is a real site checkout to mirror the public copies into, so `files/` holds
    what a live sync would put there and the built site serves the very copy its rows
    link. Without one the mirror still runs, into a throwaway directory: what the rows say
    is hosted has to come from the REAL `_mirror_public` either way, or the fixture would
    claim a copy nothing ever made."""
    real_tree, real_content = site._repo_tree, site.get_file_content
    real_spec, real_clone = site.load_grading_spec, site.clone
    real_teams = site._formed_teams
    site._repo_tree, site.get_file_content = _repo_tree, _get_file_content
    site.load_grading_spec, site.clone = _grading_spec, _clone
    site._formed_teams = _formed_teams
    try:
        with tempfile.TemporaryDirectory() as work:
            hosted = site._mirror_public(
                site_wd or Path(work), SEMESTER_ORG, PUBLISH_POLICY
            )
        return {
            "collections": collections(hosted),
            "files": {**data_files(hosted), **site_repo.theme_pages(semester=True)},
        }
    finally:
        site._repo_tree, site.get_file_content = real_tree, real_content
        site.load_grading_spec, site.clone = real_spec, real_clone
        site._formed_teams = real_teams


# The overlay the offline build layers on top of the generated `_config.yml`. The primary
# config stays byte-for-byte what a real sync writes - `remote_theme:` pin included, which
# is half of what this fixture is here to prove - and this turns the network off for the
# build, the way the theme repo's own PR check does.
OFFLINE_CONFIG = """# Fixture-only overlay - see build_fixture.py.
#
#   bundle exec jekyll build --config _config.yml,_config.offline.yml
#
# The theme is NOT fetched: `_layouts/default.html`, `page.html` and `post.html` are
# stubbed in the fixture, so the build exercises the course templates in templates/site/
# and nothing else. jekyll-remote-theme is not installed either (see the Gemfile), so the
# plugin list has to go with it.
plugins: []
remote_theme: ""
"""


def _clone(_org: str, repo: str, dest, branch=None, shallow=False) -> bool:
    """A `ghcli.clone` stand-in: the semester repo's released tree as real (tiny) files, so
    the mirror below has bytes to copy. Every path of TREE, whether published or not - the
    policy is what decides, and that decision is the code under test."""
    for rel in TREE if repo == MATERIALS else ():
        f = Path(dest) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"fixture {rel}\n", encoding="utf-8")
    return True


def build(dest: Path) -> None:
    """Write the whole fixture site into `dest`, replacing whatever was there."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(HERE / "base", dest)

    out = generated(dest)
    # Written through the sync's OWN apply step, so the site CI builds is assembled the
    # way a real one is - config upsert, collection regeneration, front-matter stamp and
    # all - rather than by a second copy of it here that can drift out of step.
    site_repo.apply_plan(
        dest,
        site_repo.SitePlan(
            config={
                "course_name": "Deep Learning (Fixture)",
                "course_description": (
                    "A fixture course. Nothing here is a real offering."
                ),
                "course_semester": "Fall 2026",
                "course_code": "E1234",
                "github_org": SEMESTER_ORG,
            },
            collections=out["collections"],
            files={**out["files"], **site_repo.site_templates()},
            commit="fixture: build the CI site",
        ),
    )
    (dest / "_config.offline.yml").write_text(OFFLINE_CONFIG, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    dest = Path(sys.argv[1]).resolve()
    build(dest)
    print(f"fixture site written to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
