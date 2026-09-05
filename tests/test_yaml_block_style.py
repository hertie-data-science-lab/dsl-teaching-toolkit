"""The faculty-facing YAML surface is block style (indent-only) - never a flow mapping
used as a list item (`- {a: 1, b: 2}`).

Flow and block parse identically, so this is a teaching/readability standard rather than a
correctness one: `schedule.yml`, `people.yml`, `dsl-course.yml`, `grading_config.yml` and the docs
that mirror them are read and hand-edited by course teams, and one shape everywhere is what
makes them copyable. The guard matters most for the SEEDED templates - a flow item left in
`templates/classroom-config/schedule.yml` is `.format()`ed into every new cohort org, so the
style regression ships to real courses.

Deliberately NOT covered: GitHub Actions workflows and Issue Forms (see EXCLUDED). Those are
machine infrastructure the docs tell faculty not to edit, they use GitHub-schema idioms where
flow style is conventional (`branches: [main]`, `workflow_dispatch: {}`), and their embedded
`github-script` bodies are JavaScript, not YAML.

Scoped to flow mappings used as LIST ITEMS. A flow mapping as a scalar value
(`deploy: {course_source_repo: ...}`) is left alone - `docs/DEPLOYMENT-CHECKLIST.md` uses one
on purpose to show a single-copy shorthand.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from dsl_course import bootstrap_course, welcome
from dsl_course.grades import (
    GradeRow,
    SheetSpec,
    build_gradebooks,
    dump_sheet,
    new_sheet,
    render_yaml,
)
from dsl_course.scaffold import _GRADING_YML

ROOT = Path(__file__).resolve().parents[1]

# `#` allowed: the seeded schedule.yml/people.yml scaffolds are commented-out YAML, which
# is exactly where the last flow item hid.
FLOW_ITEM = re.compile(r"^\s*(?:#\s*)?-\s*\{")

# Machine infrastructure - audited by hand, deliberately left in GitHub's idiom.
EXCLUDED = {
    "templates/welcome/onboard.yml",
    "templates/welcome/team-formation.yml",
    "templates/welcome/ISSUE_TEMPLATE/01-join-course.yml",
    "templates/welcome/ISSUE_TEMPLATE/02-join-team.yml",
    "templates/classroom-config/dispatch-sync.yml",
    "templates/classroom-config/dispatch-sync-site.yml",
    "templates/classroom-config/dispatch-scheduled-release.yml",
    "templates/classroom-config/dispatch-send-codes.yml",
}

FIX = (
    "flow-style mapping used as a list item. The faculty-facing YAML in this repo is block "
    "style (indent-only): rewrite `- {a: 1, b: 2}` as `- a: 1` / `  b: 2`, one key per line. "
    "Machine infrastructure (.github/workflows, templates/welcome, dispatch-*) is exempt "
    "- see EXCLUDED in this file."
)


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _scanned(p: Path) -> bool:
    """Tracked source only. A dot-directory under the repo root is local state, not
    source - `.venv`, `.pytest_cache`, `.ruff_cache`, `.git` - and letting the globs walk
    into it makes the collected test count depend on whose checkout is running. `.github`
    falls under the same rule, which is also the exemption the docstring above describes."""
    return not any(part.startswith(".") for part in p.relative_to(ROOT).parts[:-1])


def _in_scope(p: Path) -> bool:
    return _rel(p) not in EXCLUDED


def _offences(text: str, rel: str) -> list[str]:
    return [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if FLOW_ITEM.match(line)
    ]


def _fence_lines(text: str) -> list[tuple[int, str]]:
    """Lines inside ```yaml / ```yml fences, with their 1-based file line numbers."""
    out, inside = [], False
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            inside = (
                stripped[3:].strip().lower() in {"yaml", "yml"} if not inside else False
            )
            continue
        if inside:
            out.append((n, line))
    return out


YAML_FILES = sorted(p for p in ROOT.rglob("*.yml") if _scanned(p) and _in_scope(p))
MD_FILES = sorted(p for p in ROOT.rglob("*.md") if _scanned(p))
PY_FILES = sorted((ROOT / "dsl_course").glob("*.py"))


def _gradebook() -> str:
    """One student's `grades.yml` - the `yaml.safe_dump` path (grades.render_yaml)."""
    rows = {
        "assignment-1": [
            GradeRow(github_handle="janedoe", team="t1", final_grade="15")
        ],
        "assignment-2": [GradeRow(github_handle="janedoe", final_grade="10")],
    }
    books = build_gradebooks(rows)
    return render_yaml({"student": "janedoe", "assignments": books["janedoe"]})


def _grading_sheet_dump() -> str:
    """One assignment's grading sheet - the `_SheetDumper` path (grades.dump_sheet). It has
    its own dumper, so PyYAML's default is not what keeps this one block style."""
    spec = SheetSpec(
        slug="assignment-1",
        title="Neural networks from scratch",
        is_group=True,
        questions={"Q1": "15", "Q2": "10"},
        late_window_days=7,
        late_penalty_per_day="10%",
        due_display="Sun 4 Oct 2026 23:59",
        cutoff_display="Sun 11 Oct 2026 23:59",
    )
    return dump_sheet(
        new_sheet(spec, [("team-alpha", ["ada-l", "ben-k"])]),
        spec,
        "OPEN - 0 of 1 teams have submitted",
    )


def _inventory_dump() -> str:
    """`list_orgs --yaml`: dict of course/cohort lists, the shape flow style would show up in."""
    return yaml.safe_dump(
        {
            "course_orgs": [{"org": "A", "url": "https://a", "course_code": "E1"}],
            "cohort_orgs": [{"org": "A-f2026", "course": "A", "url": "https://af"}],
        },
        sort_keys=False,
    )


def _cohort_registry_dump() -> str:
    """`discovery.register_cohort`'s cohort-courses-pages.yml body."""
    return yaml.safe_dump({"cohorts": ["Demo-f2025", "Demo-f2026"]}, sort_keys=False)


# PyYAML 6 defaults `default_flow_style` to False, so these are block today and none of
# them passes it explicitly. Flow style at the document root is a single braced line with
# no `- ` at all, so FLOW_ITEM can't see it - assert on the braces/brackets instead.
DUMPED = {
    "grades-<handle>/grades.yml (grades.render_yaml)": _gradebook,
    "list_orgs --yaml inventory": _inventory_dump,
    "cohort-courses-pages.yml (discovery)": _cohort_registry_dump,
    "grading_sheets/<slug>.yml (grades.dump_sheet)": _grading_sheet_dump,
}

# Every runtime-generated faculty-facing artefact rendered from templates or string
# constants: the `.format()`ed seed templates (whose doubled `{{ }}` braces can hide a
# flow item until bootstrap renders them) and the grading_config.yml written to each
# solution branch.
SEEDED = {
    "classroom-config/schedule.yml (seeded)": lambda: welcome.template(
        "classroom-config/schedule.yml"
    ).format(tag="f2026", year=2026),
    "classroom-config/people.yml (seeded)": lambda: welcome.template(
        "classroom-config/people.yml"
    ).format(year=2026, year_next=2027),
    "course/dsl-course.yml (seeded, commented)": lambda: (
        bootstrap_course._course_metadata("Org", "Org Name", "Course", "CODE")
    ),
    "course/dsl-course.yml (seeded, --admins)": lambda: (
        bootstrap_course._course_metadata(
            "Org", "Org Name", "Course", "CODE", admins=["adminhandle"]
        )
    ),
    "cohort/dsl-course.yml (seeded)": lambda: bootstrap_course._cohort_metadata(
        "Org", "Course"
    ),
    "grading_config.yml (scaffolded)": lambda: _GRADING_YML.format(
        kind="group", fmt="notebook"
    ),
}


def test_the_sweep_actually_sees_files():
    """A broken glob would make every assertion below vacuously pass."""
    assert len(YAML_FILES) >= 8
    assert len(MD_FILES) >= 10
    assert len(PY_FILES) >= 20


def test_the_sweep_stays_out_of_dot_directories():
    """Otherwise what is collected depends on local state - a `.venv` in the checkout, or
    whether pytest has already written its cache - and the count differs per machine."""
    assert not _scanned(ROOT / ".venv" / "lib" / "site-packages" / "pkg" / "README.md")
    assert not _scanned(ROOT / ".pytest_cache" / "README.md")
    assert not _scanned(ROOT / ".github" / "workflows" / "ci.yml")
    assert _scanned(ROOT / "docs" / "README.md")
    assert _scanned(ROOT / "README.md")


@pytest.mark.parametrize("path", YAML_FILES, ids=_rel)
def test_faculty_yaml_is_block_style(path: Path):
    found = _offences(path.read_text(), _rel(path))
    assert not found, f"{FIX}\n" + "\n".join(found)


@pytest.mark.parametrize("path", MD_FILES, ids=_rel)
def test_docs_yaml_fences_are_block_style(path: Path):
    rel = _rel(path)
    found = [
        f"{rel}:{n}: {line.strip()}"
        for n, line in _fence_lines(path.read_text())
        if FLOW_ITEM.match(line)
    ]
    assert not found, f"{FIX}\n" + "\n".join(found)


@pytest.mark.parametrize("path", PY_FILES, ids=_rel)
def test_embedded_yaml_examples_are_block_style(path: Path):
    """The schema docs in module docstrings (schedule.py's is the `--help` text) and the
    YAML written from string constants."""
    found = _offences(path.read_text(), _rel(path))
    assert not found, f"{FIX}\n" + "\n".join(found)


@pytest.mark.parametrize("label", sorted(SEEDED))
def test_seeded_artefacts_are_block_style(label: str):
    found = _offences(SEEDED[label](), label)
    assert not found, f"{FIX}\n" + "\n".join(found)


@pytest.mark.parametrize("label", sorted(DUMPED))
def test_safe_dump_paths_emit_block_style(label: str):
    """No flow indicator anywhere in the dumped text. If PyYAML's `default_flow_style`
    default ever flips, this is what catches it - the fix is to pass
    `default_flow_style=False` explicitly at the dump call site."""
    out = DUMPED[label]()
    braces = sorted({c for c in "{}[]" if c in out})
    assert not braces, (
        f"{label}: yaml.safe_dump emitted flow style (found {braces}). Pass "
        f"default_flow_style=False at the dump call site.\n{out}"
    )


def test_excluded_infrastructure_paths_all_exist():
    """Keeps the exemption list honest - a renamed workflow must be re-decided, not
    silently carried."""
    missing = [rel for rel in sorted(EXCLUDED) if not (ROOT / rel).is_file()]
    assert not missing, f"EXCLUDED lists paths that no longer exist: {missing}"
