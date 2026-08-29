#!/usr/bin/env python3
"""Write a complete cohort site into a directory, from the real renderers.

What the `jekyll-contract` CI job builds (.github/workflows/ci.yml), and what
tests/test_site_templates.py cross-checks the templates' Liquid keys against. Both read
the SAME site, so the offline key check and the real Jekyll build cannot disagree about
what the sync writes.

Generated, never hand-written: every collection page, `_data/people.yml`, `_data/nav.yml`
and `_data/materials.yml` come out of `dsl_course.site`'s own functions, so the fixture
cannot drift into a shape the toolkit never produces. Only the half a site brings with it
- `_config.yml`, the theme's `default`/`page`/`post` layouts, `_data/late_policy.yml`,
`index.md`, `schedule.md` - is vendored, under `base/`.

The states it covers are the ones that render DIFFERENTLY, one of each: a released
session, an unreleased one, a lab, a session whose readings are still to come, a
handed-out assignment and a pending one, a dated exam and a TBC one, a special event, the
two term boundaries, and an All Materials index nested three directories deep.

    python3 tests/fixtures/site/build_fixture.py <dest>
"""

from __future__ import annotations

import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from dsl_course import schedule_plan, site, site_repo

BERLIN = ZoneInfo("Europe/Berlin")
COURSE_ORG = "hertie-dsl-fixture-course"
COHORT_ORG = "hertie-dsl-fixture-f2026"
MATERIALS = "course-materials"
# The moment the fixture is rendered "at", so a handout pin is in the past or the future
# by construction rather than by when CI happens to run.
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=BERLIN)

# The cohort's released tree, as `_repo_tree` would report it. Three directories deep
# under `lectures/01_week-1/`, which is the nesting the All Materials index recurses over
# - the include that once rendered an empty page and took two live sites down.
TREE = (
    "SYLLABUS.md",
    "labs/01_week-1/Lab_Session_1.ipynb",
    "lectures/01_week-1/demo.py",
    "lectures/01_week-1/handouts/extra/further-notes.pdf",
    "lectures/01_week-1/slides.pdf",
    "lectures/02_week-2/slides.pdf",
    "readings/01_week-1/READINGS.md",
    "readings/01_week-1/schmidhuber-1997.pdf",
)

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


def _get_file_content(_org: str, _repo: str, path: str) -> str | None:
    if path.endswith("READINGS.md"):
        return READINGS_MD
    if path == "README.md":
        return ASSIGNMENT_README
    return None


def _lectures() -> dict[str, str]:
    """The `_lectures` collection: the four session states that render differently."""
    released = schedule_plan.PlannedRow(
        when=datetime(2026, 9, 7, 10, 0, tzinfo=BERLIN),
        subtitle="What a neural network is",
        description="Perceptrons, activation functions and the chain rule.",
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
        description="Why weight sharing works.\n\nAnd where it does not.",
        readings_planned=True,
    )
    lab = schedule_plan.PlannedRow(when=datetime(2026, 9, 9, 14, 0, tzinfo=BERLIN))
    return {
        # Released, with a reading list inlined off the released READINGS.md overlay.
        "session-01.md": site._lecture_entry(
            COHORT_ORG,
            "1",
            released,
            [
                (MATERIALS, "lectures", "01_week-1"),
                (MATERIALS, "readings", "01_week-1"),
            ],
        ),
        # Released, but the plan's readings have not landed -> readings_pending.
        "session-02.md": site._lecture_entry(
            COHORT_ORG, "2", pending_readings, [(MATERIALS, "lectures", "02_week-2")]
        ),
        # Nothing shipped -> unreleased, and the row names where it will land.
        "session-03.md": site._lecture_entry(
            COHORT_ORG,
            "3",
            unreleased,
            [],
            live_repos=frozenset({MATERIALS}),
        ),
        "lab-01.md": site._lecture_entry(
            COHORT_ORG, "1", lab, [(MATERIALS, "labs", "01_week-1")], kind="lab"
        ),
    }


def _assignments() -> dict[str, str]:
    """One handed out (repo link, brief, README-derived name) and one still pending."""
    return {
        "01-assignment-1.md": site._assignment_entry(
            COURSE_ORG,
            COHORT_ORG,
            "assignment-1-f2026",
            datetime(2026, 10, 20, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 9, 29, 9, 0, tzinfo=BERLIN),
            handed_out=frozenset({"assignment-1"}),
            now=NOW,
        ),
        "02-assignment-2.md": site._assignment_entry(
            COURSE_ORG,
            COHORT_ORG,
            "assignment-2-f2026",
            datetime(2026, 11, 24, 23, 59, tzinfo=BERLIN),
            handout=datetime(2026, 11, 3, 9, 0, tzinfo=BERLIN),
            now=NOW,
        ),
    }


def _events() -> dict[str, str]:
    """A dated exam, a TBC one, a special event and the two term boundaries."""
    end = date(2026, 12, 18)
    return {
        "01-midterm-exam.md": site._exam_entry(
            "MidTerm Exam", datetime(2026, 11, 2, 9, 0, tzinfo=BERLIN)
        ),
        "02-guest-lecture.md": site._special_event_entry(
            "Guest lecture: forecasting at scale",
            datetime(2026, 10, 12, 16, 0, tzinfo=BERLIN),
        ),
        "03-resit-exam.md": site._exam_entry(
            "Resit Exam", end, tbc=True, dateless=True
        ),
        "term-start.md": site._term_date_entry("Term starts", date(2026, 9, 7)),
        "term-end.md": site._term_date_entry("Term ends", end),
    }


def collections() -> dict[str, dict[str, str]]:
    """The generated collections, front-matter stamp and all - exactly what
    `_sync_site_repo` writes into the site repo."""
    return {
        "_lectures": _lectures(),
        "_assignments": _assignments(),
        "_events": _events(),
    }


def data_files() -> dict[str, str]:
    """The generated `_data/*.yml`, keyed by repo-relative path."""
    return {
        "_data/people.yml": site_repo.people_yaml(
            COHORT_ORG, PEOPLE, edit_at=f"{COHORT_ORG}/classroom-config/people.yml"
        ),
        "_data/nav.yml": site_repo.nav_yaml(cohort=True),
        "_data/materials.yml": site._materials_index(
            COHORT_ORG,
            [MATERIALS],
            syllabus=f"https://github.com/{COHORT_ORG}/{MATERIALS}/blob/main/SYLLABUS.md",
        ),
    }


def generated() -> dict[str, dict[str, str] | dict[str, dict[str, str]]]:
    """Everything the sync writes, with the gh reads stubbed out and put back.

    Reassigning the module globals rather than passing fakes down: `_repo_tree` is
    memoised and `get_file_content` is imported into `site`'s namespace, so this is the
    seam every caller below actually goes through."""
    real_tree, real_content = site._repo_tree, site.get_file_content
    site._repo_tree, site.get_file_content = _repo_tree, _get_file_content
    try:
        return {
            "collections": collections(),
            "files": {**data_files(), **site_repo.theme_pages(cohort=True)},
        }
    finally:
        site._repo_tree, site.get_file_content = real_tree, real_content


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


def build(dest: Path) -> None:
    """Write the whole fixture site into `dest`, replacing whatever was there."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(HERE / "base", dest)

    out = generated()
    for coll, entries in out["collections"].items():
        (dest / coll).mkdir(parents=True, exist_ok=True)
        for name, body in entries.items():
            (dest / coll / name).write_text(
                site_repo._stamp_front_matter(body), encoding="utf-8"
            )
    for rel, body in {**out["files"], **site_repo.site_templates()}.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    cfg = (dest / "_config.yml").read_text(encoding="utf-8")
    for key, value in {
        "course_name": "Deep Learning (Fixture)",
        "course_description": "A fixture course. Nothing here is a real offering.",
        "course_semester": "Fall 2026",
        "course_code": "E1234",
        "github_org": COHORT_ORG,
    }.items():
        cfg = site_repo._replace_config_scalar(cfg, key, value)
    for key, value in site_repo._THEME_CONFIG.items():
        cfg = site_repo._upsert_config(cfg, key, f'{key}: "{site_repo.q(value)}"')
    cfg = site_repo._upsert_config(cfg, "collections", site_repo._COLLECTIONS_BLOCK)
    cfg = site_repo._upsert_config(cfg, "defaults", site_repo._DEFAULTS_BLOCK)
    (dest / "_config.yml").write_text(cfg, encoding="utf-8")
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
