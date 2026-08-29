"""Which ref of the central toolkit an org runs, and that every seeded workflow says so.

Every workflow in every bootstrapped org checks this repo out at run time, so the ref in
its checkout step IS the deployment tier that org is on. These tests hold the two halves
together: what `central_ref:` in a course org's dsl-course.yml resolves to, and that the
resolved value reaches every file the toolkit writes into an org.
"""

from __future__ import annotations

import yaml

from dsl_course import (
    central,
    discovery,
    profile_readme,
    seed,
    welcome,
    workflows_place,
)
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


# ------------------------------------------------- and that the ref is THERE before use


def _api_calls(monkeypatch, answer: tuple[int, str]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        central, "gh", lambda *args, **k: (calls.append(args), answer)[1]
    )
    return calls


def test_a_tier_branch_that_exists_is_checked_as_a_branch(monkeypatch):
    calls = _api_calls(monkeypatch, (0, ""))
    assert central.central_ref_exists("release") is True
    assert calls == [("api", f"repos/{CENTRAL}/branches/release", "--silent")]


def test_a_pinned_sha_is_checked_as_a_commit(monkeypatch):
    # `branches/<sha>` 404s for a perfectly good commit, so the endpoint has to follow
    # what the ref IS - otherwise every SHA-pinned org reads as bricked.
    calls = _api_calls(monkeypatch, (0, ""))
    assert central.central_ref_exists(SHA) is True
    assert calls == [("api", f"repos/{CENTRAL}/commits/{SHA}", "--silent")]


def test_a_missing_ref_is_reported_as_missing(monkeypatch):
    _api_calls(monkeypatch, (1, "gh: Not Found (HTTP 404)"))
    assert central.central_ref_exists("release") is False


def test_a_ref_that_cannot_be_checked_is_assumed_present(monkeypatch, capsys):
    # A rate limit or a 502 must not stall every org's convergence; proceeding is only
    # ever the behaviour that stood before this check existed.
    _api_calls(monkeypatch, (1, "HTTP 502 Bad Gateway"))
    assert central.central_ref_exists("release") is True
    assert "assuming it does" in capsys.readouterr().err


def _refresh_against(monkeypatch, ref_exists: bool) -> tuple[int, list[str]]:
    """`seed.refresh`'s exit code, and which of its renderers actually ran."""
    rendered: list[str] = []

    def renders(name: str):
        """A renderer double that pins the ref exactly as the real one does - through the
        one chokepoint that refuses a ref the central repo does not have."""

        def step(*args) -> int:
            central.pin_central_ref("", args[-1])
            rendered.append(name)
            return 0

        return step

    monkeypatch.setattr(seed, "central_ref_for", lambda org: "release")
    monkeypatch.setattr(
        central,
        "gh",
        lambda *a, **k: (0, "") if ref_exists else (1, "gh: Not Found (HTTP 404)"),
    )
    monkeypatch.setattr(seed, "_live_cohorts", lambda org: (["Cohort-f2026"], 0))
    monkeypatch.setattr(seed, "discover_content_repos", lambda org: ["materials-f2026"])
    monkeypatch.setattr(seed, "discover_assignments", lambda org: [])
    monkeypatch.setattr(seed, "push_content_workflows", renders("content-workflows"))
    monkeypatch.setattr(seed, "_refresh_stubs", lambda org, repo: 0)
    monkeypatch.setattr(
        seed.scaffold, "refresh_materials_system_files", lambda org, repo: 0
    )
    monkeypatch.setattr(seed, "_propagate_repo_secret", lambda org, repos: 0)
    monkeypatch.setattr(seed, "list_org_repos", lambda org: [])
    monkeypatch.setattr(seed, "_converge_org_metadata", lambda org, repos: 0)
    monkeypatch.setattr(seed, "seed_github_workflows", renders("org-workflows"))
    monkeypatch.setattr(seed, "_write_heartbeat", lambda org: 0)
    monkeypatch.setattr(seed, "update_profile_readme", lambda org, **k: 0)
    monkeypatch.setattr(seed, "refresh_welcome_workflows", lambda org: 0)
    monkeypatch.setattr(
        seed, "refresh_classroom_system_files", renders("classroom-system-files")
    )
    monkeypatch.setattr(seed, "refresh_classroom_samples", lambda org: 0)
    monkeypatch.setattr(seed, "refresh_cohort_pointer", lambda org, course: 0)
    return seed.refresh("Course-Org"), rendered


def test_refresh_renders_the_workflows_when_the_ref_is_there(monkeypatch):
    code, rendered = _refresh_against(monkeypatch, ref_exists=True)
    assert code == 0
    assert rendered == [
        "content-workflows",
        "org-workflows",
        "classroom-system-files",
    ]


def test_refresh_refuses_to_render_workflows_at_a_ref_that_does_not_exist(
    monkeypatch, capsys
):
    # Rendering a missing ref writes a checkout nothing can satisfy into EVERY workflow
    # in the org, Refresh included - so the org loses the one button that would heal it.
    # Leaving last night's rendering in place keeps the org running on stale-but-working
    # workflows; the failure count turns the cron red so somebody creates the ref.
    code, rendered = _refresh_against(monkeypatch, ref_exists=False)
    assert code == 1
    assert rendered == []
    err = capsys.readouterr().err
    assert "release" in err and "Course-Org" in err
