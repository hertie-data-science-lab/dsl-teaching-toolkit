"""Decision 0012's renames: every new spelling is the one written and the only one read,
and every old one is REFUSED as NOT_MIGRATED - naming the new spelling - wherever it turns
up: an instructor file, a key, a topic, a request, a dispatch. One section per rename, in
the order of `maintainers.md`'s "Migration" table."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from conftest import workflow_inputs
from test_renderers import ALL_RENDERED

from dsl_course import (
    assign,
    bootstrap_course,
    discovery,
    grades,
    list_orgs,
    schedule,
    schemas,
    site,
    status,
    status_json,
    sync_faculty,
    welcome,
    workflows_render,
)
from dsl_course.course import INSTRUCTOR_ROLES, people_by_role
from dsl_course.faults import NOT_MIGRATED, NotMigrated
from dsl_course.ops.registry import REGISTRY
from dsl_course.ops.request import RequestError, parse_request

# ------------------------------------------------------------------ semester (cohort)


def _gh(topic: str) -> dict:
    return {"name": ".github", "topics": [topic]}


def test_the_semester_topic_places_an_org_and_the_old_one_is_refused():
    assert discovery.org_tier([_gh("dsl-semester")]) == "semester"
    with pytest.raises(NotMigrated, match=NOT_MIGRATED):
        discovery.org_tier([_gh("dsl-cohort")])


def test_the_inventory_names_an_org_on_the_old_topic_and_goes_partial(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        list_orgs,
        "_tagged_orgs",
        lambda topic: {"dsl-cohort": ["Old-Sem"], "dsl-semester": ["New-Sem"]}[topic],
    )
    monkeypatch.setattr(list_orgs, "_metadata_or_none", lambda org: {"course": "C"})
    found = {s["org"]: s["readable"] for s in list_orgs.discover_semester_orgs()}
    assert found == {"Old-Sem": False, "New-Sem": True}
    assert NOT_MIGRATED in capsys.readouterr().err


def _registry(monkeypatch, files: dict[str, str]) -> list[tuple[str, bytes]]:
    written: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        discovery, "get_file_content", lambda org, repo, path: files.get(path)
    )
    monkeypatch.setattr(
        discovery,
        "put_file",
        lambda org, repo, path, body, msg: written.append((path, body)) or True,
    )
    return written


@pytest.mark.parametrize(
    "files",
    [
        {"cohort-courses-pages.yml": "cohorts:\n- Sem-f2026\n"},
        {"semesters.yml": "cohorts:\n- Sem-f2026\n"},
    ],
)
def test_the_old_registry_file_or_key_is_refused(monkeypatch, files):
    _registry(monkeypatch, files)
    with pytest.raises(NotMigrated):
        discovery.discover_semesters("Course")
    found: list = []
    assert discovery.read_semester_registry("Course", found) == []
    assert [f.code for f in found] == [NOT_MIGRATED]


def test_registering_writes_the_new_registry_file(monkeypatch):
    written = _registry(monkeypatch, {"semesters.yml": "semesters: [A]\n"})
    assert discovery.register_semester("Course", "B")
    assert [(p, b.decode()) for p, b in written] == [
        ("semesters.yml", "semesters:\n- A\n- B\n")
    ]


def test_an_assignment_naming_the_old_dest_key_is_dropped_as_not_migrated():
    sched = schedule.parse(
        {
            "assignments": {
                "hw": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "cohort_dest_repo": "homework-1",
                },
                "ok": {
                    "course_source_repo": "b-f2026",
                    "due_datetime": "2026-10-13",
                    "semester_dest_repo": "homework-2",
                },
            }
        }
    )
    assert list(sched.assignments) == ["ok"]
    assert sched.assignments["ok"].semester_dest_repo == "homework-2"
    assert [f.code for f in sched.faults if f.code] == [NOT_MIGRATED]


def test_a_deploy_naming_the_old_dest_keys_ships_nothing():
    sched = schedule.parse(
        {
            "releases": {
                "lecture-1": {
                    "event_datetime": "2026-09-08T09:00",
                    "deploy": [
                        {
                            "course_source_repo": "course-materials-f2026",
                            "course_source_path": "lectures/01",
                            "cohort_dest_repo": "slides",
                        }
                    ],
                }
            }
        }
    )
    assert [d for r in sched.releases for d in r.deploy] == []
    assert any(NOT_MIGRATED in line for line in sched.dropped)


def _request(**over) -> str:
    raw = {
        "schema": "dsl.request/1",
        "op": "release.now",
        "actor": "prof",
        "course_org": "Course",
        "args": {"entry": "s5"},
        "preview": True,
    }
    raw.update(over)
    return json.dumps(raw)


def test_a_request_takes_semester_org_and_refuses_cohort_org():
    assert parse_request(_request(semester_org="Sem")).semester_org == "Sem"
    with pytest.raises(RequestError) as refused:
        parse_request(_request(cohort_org="Sem"))
    assert refused.value.code == NOT_MIGRATED


@pytest.mark.parametrize(
    "op, args",
    [
        ("release.now", {"entry": "s5", "cohort_dest_repo": "slides"}),
        ("materials.create", {"tag": "f2026"}),
        ("assignment.create", {"number": "1", "semester": "f2026", "format": "py"}),
        (
            "assignment.handout_now",
            {"course_source_repo": "a1", "include_solution": True},
        ),
    ],
)
def test_an_old_request_arg_is_refused_as_not_migrated(op, args):
    with pytest.raises(RequestError) as refused:
        parse_request(_request(op=op, semester_org="Sem", args=args))
    assert refused.value.code == NOT_MIGRATED


@pytest.mark.parametrize("flag", ["--semester-org", "--cohort-org"])
def test_a_cli_takes_only_the_new_flag(monkeypatch, flag):
    seen: list[tuple] = []
    monkeypatch.setattr(status, "refresh", lambda *a: seen.append(a) or 0)
    monkeypatch.setattr(
        sys, "argv", ["status", "--course-org", "C", flag, "Sem", "--write"]
    )
    if flag == "--cohort-org":
        with pytest.raises(SystemExit):
            status.main()
    else:
        assert status.main() == 0
        assert seen == [("C", "Sem")]


def test_bootstrap_refuses_the_old_cohort_defaults_block(monkeypatch):
    monkeypatch.setattr(
        bootstrap_course, "org_meta", lambda org: {"cohort_defaults": {}}
    )
    with pytest.raises(NotMigrated):
        bootstrap_course.course_semester_defaults("C")


@pytest.mark.parametrize(
    "render", ["render_sync_membership", "render_send_codes", "render_scheduler"]
)
def test_a_dispatch_sending_the_old_payload_names_fails_its_step(render):
    fn = getattr(workflows_render, render)
    rendered = fn(["A"]) if render == "render_sync_membership" else fn()
    assert "client_payload.cohort_org" in rendered  # read only to be refused
    assert "::error::NOT_MIGRATED" in rendered


def test_status_json_says_semester_and_never_cohort():
    semester = schemas.status_schema()["properties"]["semester"]["properties"]
    assert {"key", "label"} <= set(semester)
    assert "cohort" not in schemas.status_schema()["properties"]


# --------------------------------------------------------- instructors.yml (people.yml)

NEW_INSTRUCTORS = {
    "instructors": [
        {"github_handle": "prof", "role": "instructor", "email": "p@x.org"},
        {"github_handle": "ta", "role": "teaching_assistant", "email": "t@x.org"},
        {"github_handle": "nobody", "email": "n@x.org"},
    ]
}
OLD_PEOPLE = {"people": {"instructors": [{"github_handle": "old", "email": "o@x.org"}]}}


def test_the_instructors_list_is_grouped_by_role_and_a_missing_role_is_a_fault():
    found: list = []
    faculty = sync_faculty.parse_faculty_from_meta(NEW_INSTRUCTORS, found)
    assert [p["github_handle"] for p in faculty["instructors"]] == ["prof"]
    assert [p["github_handle"] for p in faculty["teaching_assistants"]] == ["ta"]
    assert [(f.where, f.field) for f in found] == [("instructors[2]", "role")]


def _semester_files(monkeypatch, files: dict[str, dict]) -> None:
    monkeypatch.setattr(
        sync_faculty,
        "load_yaml_config",
        lambda org, repo, path, lines=False: files.get(path),
    )


@pytest.mark.parametrize(
    "files, cited",
    [
        ({"people.yml": OLD_PEOPLE}, "people.yml"),
        ({"instructors.yml": OLD_PEOPLE}, "instructors.yml"),
    ],
)
def test_the_old_file_or_shape_is_refused_and_nobody_is_pruned(
    monkeypatch, files, cited
):
    _semester_files(monkeypatch, files)
    found: list = []
    assert sync_faculty.read_semester_people("Sem", found) is None
    assert [(f.code, f.file) for f in found] == [(NOT_MIGRATED, cited)]
    with pytest.raises(NotMigrated):
        sync_faculty.load_semester_faculty("Sem")


def test_the_new_instructors_file_is_read(monkeypatch):
    _semester_files(monkeypatch, {"instructors.yml": NEW_INSTRUCTORS})
    faculty = sync_faculty.read_semester_people("Sem", [])
    assert [p["github_handle"] for p in faculty["instructors"]] == ["prof"]


def test_the_site_cards_read_instructors_yml_only(monkeypatch):
    files = {"people.yml": OLD_PEOPLE, "instructors.yml": NEW_INSTRUCTORS}
    monkeypatch.setattr(site, "yaml_file", lambda org, repo, path: files.get(path, {}))
    meta, path = site._instructors_meta("Sem")
    assert path == "instructors.yml"
    grouped = people_by_role(meta)
    assert [p["github_handle"] for p in grouped["teaching_assistants"]] == ["ta"]


def test_a_new_semester_is_seeded_instructors_yml_and_never_people_yml():
    assert "instructors.yml" in welcome.CLASSROOM_SCAFFOLDS
    assert "people.yml" not in welcome.CLASSROOM_SCAFFOLDS
    assert "people.yml.sample" not in welcome.CLASSROOM_SAMPLES
    shipped = yaml.safe_load(welcome.example_semester_file("instructors.yml"))
    assert {p["role"] for p in shipped["instructors"]} == set(INSTRUCTOR_ROLES)


def test_the_exported_instructors_schema_requires_a_role():
    entry = schemas.instructors_schema()["properties"]["instructors"]["items"]
    assert "role" in entry["required"]
    assert entry["properties"]["role"]["enum"] == list(INSTRUCTOR_ROLES)


def test_a_not_migrated_fault_carries_its_own_problem_code():
    from datetime import UTC, datetime

    from dsl_course.faults import not_migrated_fault

    fault = not_migrated_fault(
        "people.yml", "instructors.yml", where="x", file="people.yml"
    )
    problem = status_json.problem_from_fault(fault, "Sem", datetime.now(UTC))
    assert NOT_MIGRATED in problem["id"]


# ------------------------------------------- grading_cutoff_datetime (late_until, pins)


def _one_assignment(**extra) -> schedule.Schedule:
    entry = {"course_source_repo": "a-f2026", "due_datetime": "2026-10-13T18:00"}
    return schedule.parse({"assignments": {"a1": {**entry, **extra}}})


def test_the_one_cutoff_resolver_adds_the_late_window_to_the_due_date():
    sched = _one_assignment()
    due = sched.assignments["a1"].due_datetime
    assert schedule.grading_cutoff_datetime(sched, "a1") == due
    assert schedule.grading_cutoff_datetime(sched, "a1", 3) == due + timedelta(days=3)
    assert schedule.grading_cutoff_datetime(sched, "nope", 3) is None


def test_the_old_resolvers_are_gone():
    assert not hasattr(schedule, "grading_datetime_at")
    assert not hasattr(grades, "cutoff_at")


def test_status_json_names_the_cutoff_grading_cutoff_datetime():
    row = schemas.status_schema()["properties"]["assignments"]["items"]
    assert "grading_cutoff_datetime" in row["required"]
    assert "late_until" not in row["properties"]


# ------------------------------------------- solution_datetime (include_solution, --solution)


def _hand_out(monkeypatch, *flags: str) -> list[bool]:
    seen: list[bool] = []
    monkeypatch.setattr(
        assign,
        "provision_all",
        lambda *a, solution=False, **k: seen.append(solution) or (0, 0),
    )
    monkeypatch.setattr(assign, "listing_by_name", lambda org: None)
    base = [
        "assign",
        "--master-org",
        "C",
        "--course-source-repo",
        "a1",
        "--semester-org",
        "S",
    ]
    monkeypatch.setattr(sys, "argv", [*base, *flags])
    assert assign.main() == 0
    return seen


@pytest.mark.parametrize(
    "flags, pushed", [((), False), (("--solution-datetime", "now"), True)]
)
def test_the_manual_hand_out_includes_the_solution_only_when_asked(
    monkeypatch, flags, pushed
):
    assert _hand_out(monkeypatch, *flags) == [pushed]


@pytest.mark.parametrize(
    "flags", [("--solution-datetime", "2026-12-01"), ("--solution",)]
)
def test_a_later_moment_or_the_old_switch_is_refused(monkeypatch, flags):
    with pytest.raises(SystemExit):
        _hand_out(monkeypatch, *flags)


def test_the_hand_out_op_passes_solution_datetime_now():
    req = parse_request(
        _request(
            op="assignment.handout_now",
            semester_org="S",
            args={"course_source_repo": "a1", "solution_datetime": "now"},
        )
    )
    assert REGISTRY["assignment.handout_now"].argv(req)[-2:] == [
        "--solution-datetime",
        "now",
    ]


# ------------------------------------------------------------------- formats (format)


def test_formats_is_a_list_and_its_first_entry_is_the_runnable_one():
    spec = grades.parse_grading_spec("formats: [py, ipynb]\n")
    assert (spec.formats, spec.format, spec.dropped) == (("py", "ipynb"), "py", ())


def test_the_old_format_key_is_refused_and_not_read():
    spec = grades.parse_grading_spec("format: ipynb\n")
    assert spec.formats == ()
    assert [(d.field, d.code) for d in spec.dropped] == [("format", NOT_MIGRATED)]


def test_a_course_default_under_the_old_format_key_is_not_read():
    assert grades.parse_assignment_defaults({"format": "py"}) == {}
    assert grades.parse_assignment_defaults({"formats": "py"}) == {"formats": "py"}


# ------------------------------------------------ preview (dry_run, write) and notify


def _preview_clis() -> set[str]:
    root = Path(__file__).resolve().parents[1] / "dsl_course"
    return {p.stem for p in root.glob("*.py") if "add_preview_flag(" in p.read_text()}


def test_every_cli_previews_unless_told_otherwise(monkeypatch):
    seen: list[bool] = []
    monkeypatch.setattr(
        assign, "provision_all", lambda *a, dry_run, **k: seen.append(dry_run) or (0, 0)
    )
    monkeypatch.setattr(assign, "listing_by_name", lambda org: None)
    base = [
        "assign",
        "--master-org",
        "C",
        "--course-source-repo",
        "a1",
        "--semester-org",
        "S",
    ]
    for extra, want in (((), True), (("--no-preview",), False)):
        monkeypatch.setattr(sys, "argv", [*base, *extra])
        assign.main()
        assert seen.pop() is want
    monkeypatch.setattr(sys, "argv", [*base, "--dry-run"])
    with pytest.raises(SystemExit):
        assign.main()


def test_every_rendered_run_of_a_previewing_cli_says_which_it_is():
    # The CLI default is preview, so a rendered step that spelt neither flag would preview
    # for ever on a green run - a cron that released nothing, a roster push that sent no
    # codes. Every step that runs one of those CLIs spells `--preview` or `--no-preview`.
    clis = _preview_clis()
    assert {"scheduler", "assign", "enrol_codes", "sync_membership"} <= clis
    for name, rendered in ALL_RENDERED.items():
        for job in (yaml.safe_load(rendered).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                run = str(step.get("run") or "")
                for cli in clis:
                    if (
                        f"-m dsl_course.{cli} " in run
                        or f"-m dsl_course.{cli}\n" in run
                    ):
                        assert "--no-preview" in run or "--preview" in run, (name, cli)


def test_every_button_previews_by_default_and_no_old_box_is_left():
    boxed = 0
    for name, rendered in ALL_RENDERED.items():
        doc = yaml.safe_load(rendered)
        if "workflow_dispatch" not in (doc.get("on", doc.get(True)) or {}):
            continue
        inputs = workflow_inputs(rendered)
        assert not {"dry_run", "write", "silent"} & set(inputs), name
        if "preview" in inputs:
            boxed += 1
            assert inputs["preview"]["default"] is True, name
    assert boxed >= 10


def test_every_op_with_a_preview_acts_only_when_told_no_preview():
    for op in REGISTRY.values():
        if op.preview_flag:
            assert op.preview_flag == "--preview", op.name
            assert op.real_flag in ("--no-preview", None), op.name
    assert REGISTRY["cohort.preview_automation"].real_flag is None


def test_distribute_says_notify_and_only_false_holds_the_mail():
    rendered = ALL_RENDERED["distribute_grades"]
    assert workflow_inputs(rendered)["notify"]["default"] is True
    assert '[ "$NOTIFY" = "false" ] && args+=(--no-notify)' in rendered
