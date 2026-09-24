"""The pause: an org variable every job of every seeded workflow checks first, so the
migration can rewrite an org with nothing running against it."""

from __future__ import annotations

import pytest
import yaml
from test_renderers import ALL_RENDERED

from dsl_course import welcome, workflows_render
from dsl_course.central import PAUSE_VARIABLE, PAUSED, pausable

SEMESTER_WORKFLOWS = {
    **{
        path: body.decode()
        for path, body in welcome.config_system_files("main").items()
        if path.startswith(".github/workflows/")
    },
    "onboard": welcome.join_workflow("join/onboard.yml"),
    "team-formation": welcome.join_workflow("join/team-formation.yml"),
}
COURSE_WORKFLOWS = {
    name: workflows_render.for_placement(text, "main")
    for name, text in ALL_RENDERED.items()
}


@pytest.mark.parametrize(
    "text",
    [*COURSE_WORKFLOWS.values(), *SEMESTER_WORKFLOWS.values()],
    ids=[*COURSE_WORKFLOWS, *SEMESTER_WORKFLOWS],
)
def test_every_job_of_every_seeded_workflow_checks_the_pause_first(text):
    jobs = yaml.safe_load(text)["jobs"]
    assert jobs
    for name, job in jobs.items():
        assert str(job.get("if", "")).startswith(PAUSED), name


def test_a_job_keeps_its_own_condition_behind_the_pause():
    text = "jobs:\n  a:\n    if: ${{ github.event_name == 'push' }}\n    runs-on: x\n"
    assert pausable(text) == (
        f"jobs:\n  a:\n    if: {PAUSED} && (github.event_name == 'push')\n    runs-on: x\n"
    )
    assert pausable(pausable(text)) == pausable(text)


def test_a_file_without_jobs_is_left_alone():
    assert pausable("# README\n") == "# README\n"
    assert PAUSE_VARIABLE == "DSL_PAUSED"
