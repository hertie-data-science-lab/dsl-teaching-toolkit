"""Which ref of the central toolkit an org runs, and that every seeded workflow says so.

Every workflow in every bootstrapped org checks this repo out at run time, so the ref in
its checkout step IS the deployment tier that org is on. These tests hold the two halves
together: what `central_ref:` in a course org's dsl-course.yml resolves to, and that the
resolved value reaches every file the toolkit writes into an org.
"""

from __future__ import annotations

import yaml

from dsl_course import discovery, profile_readme, seed, welcome, workflows_place
from dsl_course.central import CENTRAL, CENTRAL_REF, CENTRAL_REF_PLACEHOLDER

SHA = "0" * 40


def _configs(monkeypatch, files: dict[str, dict]) -> None:
    """Stand in for every org's `.github/dsl-course.yml`, keyed by org."""
    monkeypatch.setattr(
        discovery, "load_yaml_config", lambda org, repo, path: files.get(org)
    )


# ------------------------------------------------------ what a declaration resolves to


def test_a_course_org_runs_the_tier_it_declares(monkeypatch):
    _configs(monkeypatch, {"Course": {"central_ref": "staging"}})
    assert discovery.central_ref_for("Course") == "staging"


def test_a_cohort_inherits_the_tier_of_the_course_org_it_points_at(monkeypatch):
    # A cohort's own file is a pointer, so the tier has to come from the far end of it -
    # a cohort running a different engine from the course org releasing into it is not a
    # state worth being able to reach.
    _configs(
        monkeypatch,
        {
            "Cohort-f2026": {"course": "Course", "central_ref": "main"},
            "Course": {"central_ref": "staging"},
        },
    )
    assert discovery.central_ref_for("Cohort-f2026") == "staging"


def test_an_org_that_declares_nothing_runs_the_default(monkeypatch):
    _configs(monkeypatch, {"Course": {"course_name": "Deep Learning"}})
    assert discovery.central_ref_for("Course") == CENTRAL_REF == "release"


def test_an_org_with_no_config_at_all_runs_the_default(monkeypatch):
    _configs(monkeypatch, {})
    assert discovery.central_ref_for("Course") == CENTRAL_REF


def test_a_full_sha_pins_an_org_to_one_build(monkeypatch):
    _configs(monkeypatch, {"Course": {"central_ref": SHA}})
    assert discovery.central_ref_for("Course") == SHA


def test_junk_falls_back_to_the_default_and_names_the_file(monkeypatch, capsys):
    # Rendered into the checkout step of every workflow in the org, so a typo would take
    # the whole Actions tab down at the first run, hours later, with nothing pointing at
    # the cause. Falling back keeps the org running; the [err] line says where to fix it.
    _configs(monkeypatch, {"Course": {"central_ref": "stagign"}})
    assert discovery.central_ref_for("Course") == CENTRAL_REF
    err = capsys.readouterr().err
    assert "stagign" in err
    assert "Course/.github/dsl-course.yml" in err


def test_an_abbreviated_sha_is_junk(monkeypatch, capsys):
    # actions/checkout resolves short SHAs inconsistently, so only the full 40 count.
    _configs(monkeypatch, {"Course": {"central_ref": "0" * 7}})
    assert discovery.central_ref_for("Course") == CENTRAL_REF
    assert "not one of" in capsys.readouterr().err


# ------------------------------------------- and that the resolved value reaches the org


def _central_checkout_refs(rendered: str) -> list[str]:
    """The ref of every step in a rendered workflow that checks the CENTRAL repo out."""
    doc = yaml.safe_load(rendered)
    return [
        step["with"]["ref"]
        for job in doc["jobs"].values()
        for step in job.get("steps", [])
        if (step.get("with") or {}).get("repository") == CENTRAL
    ]


def test_every_org_level_workflow_is_pinned_to_the_orgs_ref(monkeypatch):
    monkeypatch.setattr(seed, "discover_cohorts", lambda org: ["Cohort-f2026"])
    monkeypatch.setattr(
        seed, "discover_content_repos", lambda org: ["course-materials"]
    )
    monkeypatch.setattr(
        seed, "discover_assignments", lambda org: ["assignment-1-f2026"]
    )
    written: dict[str, bytes] = {}
    monkeypatch.setattr(
        seed,
        "put_files",
        lambda org, repo, files, message, **k: written.update(files) or True,
    )

    assert seed.seed_github_workflows("Course", "staging") == 0
    assert written
    for path, content in written.items():
        raw = content.decode()
        assert CENTRAL_REF_PLACEHOLDER not in raw, path
        refs = _central_checkout_refs(raw)
        assert refs and set(refs) == {"staging"}, path


def test_the_run_from_repo_buttons_are_pinned_too(monkeypatch):
    # They live in the content repos rather than in `.github`, and are written by a
    # different module - so they are the easy half to leave behind on the old tier.
    written: dict[str, bytes] = {}
    monkeypatch.setattr(
        workflows_place,
        "put_files",
        lambda org, repo, files, message, **k: written.update(files) or True,
    )

    assert (
        workflows_place.push_content_workflows(
            "Course", "course-materials-f2026", ["Cohort-f2026"], [], "staging"
        )
        == 0
    )
    assert written
    for path, content in written.items():
        refs = _central_checkout_refs(content.decode())
        assert refs and set(refs) == {"staging"}, path


def test_a_cohorts_schedule_validator_is_pinned_to_the_inherited_ref(monkeypatch):
    written: dict[str, bytes] = {}
    monkeypatch.setattr(
        welcome,
        "put_files",
        lambda org, repo, files, message, **k: written.update(files) or True,
    )

    assert welcome.refresh_classroom_system_files("Cohort-f2026", "staging") == 0
    raw = written[".github/workflows/validate-schedule.yml"].decode()
    assert CENTRAL_REF_PLACEHOLDER not in raw
    assert _central_checkout_refs(raw) == ["staging"]


def test_the_faculty_landing_page_links_the_docs_at_the_orgs_ref():
    # The runbooks describe the engine the org is actually running; a staging org sent to
    # the release docs reads instructions for code it does not have.
    page = profile_readme.render_profile_readme(
        "Course", "Course", "Deep Learning", [], False, [], central_ref="staging"
    )
    assert f"https://github.com/{CENTRAL}/blob/staging/docs/README.md" in page
    assert "/blob/release/" not in page
