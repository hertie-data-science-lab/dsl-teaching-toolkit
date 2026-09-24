"""Decision 0012's renames: every new spelling is the one written and the only one read,
and every old one is REFUSED as NOT_MIGRATED - naming the new spelling - wherever it turns
up: an instructor file, a key, a topic, a request, a dispatch. One section per rename, in
the order of `maintainers.md`'s "Migration" table."""

from __future__ import annotations

import json
import re
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
    collect,
    deploy,
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

ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ semester (cohort)


def _gh(topic: str) -> dict:
    return {"name": ".github", "topics": [topic]}


def test_the_semester_topic_places_an_org_and_the_old_one_places_nothing():
    # The old topic is never read as a tier: the org's tier cannot be told, so the
    # faculty-access sweep gives it the read floor, and its caller reports NOT_MIGRATED.
    assert discovery.org_tier([_gh("dsl-semester")]) == "semester"
    assert discovery.org_tier([_gh("dsl-cohort"), {"name": "join"}]) is None
    assert discovery.carries_old_semester_topic([_gh("dsl-cohort")])
    assert not discovery.carries_old_semester_topic([_gh("dsl-semester")])


def test_one_unmigrated_semester_does_not_stop_the_course_refresh(monkeypatch, capsys):
    from test_bootstrap_seeding import _stub_refresh

    from dsl_course import seed

    _stub_refresh(monkeypatch)
    old = [_gh("dsl-cohort")]
    monkeypatch.setattr(
        seed, "list_org_repos", lambda org: old if org == "Semester-f2026" else []
    )
    converged: list[str] = []
    monkeypatch.setattr(
        seed,
        "_converge_org",
        lambda org, ref, listing=None, is_semester=False: converged.append(org) or 0,
    )
    assert seed.refresh("Course-Org") == 0
    # Nothing is written into the unmigrated semester; the one beside it still converges.
    assert converged[-1:] == ["Semester-s2027"]
    assert "Semester-f2026" not in converged
    out = capsys.readouterr()
    assert f"::error::Semester-f2026: {NOT_MIGRATED}" in out.out
    assert NOT_MIGRATED in out.err


def test_the_inventory_names_an_org_on_the_old_topic_and_goes_partial(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        list_orgs,
        "_tagged_orgs",
        lambda topic: {"dsl-cohort": ["Old-Sem"], "dsl-semester": ["New-Sem"]}[topic],
    )
    monkeypatch.setattr(list_orgs, "_pointer_or_none", lambda org: {"course": "C"})
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
def test_a_cli_takes_only_the_new_flag(monkeypatch, capsys, flag):
    seen: list[tuple] = []
    monkeypatch.setattr(status, "refresh", lambda *a: seen.append(a) or 0)
    monkeypatch.setattr(
        sys, "argv", ["status", "--course-org", "C", flag, "Sem", "--no-preview"]
    )
    if flag == "--cohort-org":
        with pytest.raises(SystemExit):
            status.main()
        err = capsys.readouterr().err
        assert (
            f"{NOT_MIGRATED}: `--cohort-org` is the old name of `--semester-org`" in err
        )
        assert seen == []
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


def test_an_old_people_file_beside_the_seeded_skeleton_prunes_nobody(monkeypatch):
    # The seeded instructors.yml is all comments: it parses to {} - no list at all. Beside
    # a live people.yml that is a semester that has not migrated, and it must never be
    # read as an empty desired set, which a prune=True reconcile would act on.
    _semester_files(monkeypatch, {"instructors.yml": {}, "people.yml": OLD_PEOPLE})
    found: list = []
    assert sync_faculty.read_semester_people("Sem", found) is None
    assert [(f.code, f.file) for f in found] == [(NOT_MIGRATED, "people.yml")]
    reconciled: list = []
    monkeypatch.setattr(
        sync_faculty,
        "reconcile_team_members",
        lambda *a, **k: reconciled.append(a) or 0,
    )
    assert sync_faculty.sync_semester_instructors("C", "Sem", [], []) == 0
    assert reconciled == []


def test_a_skeleton_instructors_file_alone_is_a_fault_and_prunes_nobody(monkeypatch):
    _semester_files(monkeypatch, {"instructors.yml": {}})
    found: list = []
    assert sync_faculty.read_semester_people("Sem", found) is None
    assert "no `instructors:` list" in found[0].what
    reconciled: list = []
    monkeypatch.setattr(
        sync_faculty,
        "reconcile_team_members",
        lambda *a, **k: reconciled.append(a) or 0,
    )
    assert sync_faculty.sync_semester_instructors("C", "Sem", [], []) == 0
    assert reconciled == []


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
    assert "instructors.yml" in welcome.CONFIG_SCAFFOLDS
    assert "people.yml" not in welcome.CONFIG_SCAFFOLDS
    example = ROOT / "example-course" / "cohort-org" / "instructors.yml"
    shipped = yaml.safe_load(example.read_text(encoding="utf-8"))
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
        "--course-org",
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
def test_a_later_moment_or_the_old_switch_is_refused(monkeypatch, capsys, flags):
    with pytest.raises(SystemExit):
        _hand_out(monkeypatch, *flags)
    err = capsys.readouterr().err
    if flags == ("--solution",):
        # Not prefix-matched onto --solution-datetime (allow_abbrev=False): refused.
        assert f"{NOT_MIGRATED}: `--solution` is the old name of" in err
    else:
        assert "takes `now` on a manual hand out" in err


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


def test_a_template_naming_only_format_is_refused_whole(monkeypatch, capsys):
    from datetime import UTC, datetime

    with pytest.raises(NotMigrated):
        grades.parse_grading_spec("type: group\nformat: ipynb\n")
    # Beside `formats:` the old key is only a dropped line; the file is still read.
    spec = grades.parse_grading_spec("formats: [py]\nformat: ipynb\n")
    assert spec.formats == ("py",)
    assert [(d.field, d.code) for d in spec.dropped] == [("format", NOT_MIGRATED)]
    # Loaded, it is flagged rather than read as the defaults, and the handout refuses it.
    monkeypatch.setattr(grades, "_grading_text", lambda org, t: "format: ipynb\n")
    assert grades.load_grading_spec("C", "a1").not_migrated
    assert grades.declared_grading_spec("C", "a1").not_migrated
    assert assign.provision_all("C", "a1", "S") == (1, False)
    assert NOT_MIGRATED in capsys.readouterr().err
    faults, parsed = grades.grading_spec_faults(
        "a1", "a1", "C", "format: ipynb\n", datetime.now(UTC)
    )
    assert parsed is None and [f.code for f in faults] == [NOT_MIGRATED]


def test_a_course_default_under_the_old_format_key_is_not_read():
    assert grades.parse_assignment_defaults({"format": "py"}) == {}
    assert grades.parse_assignment_defaults({"formats": "py"}) == {"formats": "py"}


# ------------------------------------------------ preview (dry_run, write) and notify


class _Parsed(Exception):
    def __init__(self, parser):
        self.parser = parser


def _preview_clis(monkeypatch) -> set[str]:
    """Every CLI module whose PARSER takes `--preview`, read off the parser each `main()`
    builds - not off its source text - so a CLI that grows the flag any other way is in."""
    import importlib
    import pkgutil

    import dsl_course
    from dsl_course.log import CLIParser

    def caught(self, args=None, namespace=None):
        raise _Parsed(self)

    monkeypatch.setattr(CLIParser, "parse_known_args", caught)
    monkeypatch.setattr(sys, "argv", ["cli"])
    found = set()
    for info in pkgutil.iter_modules(dsl_course.__path__):
        module = importlib.import_module(f"dsl_course.{info.name}")
        if not callable(getattr(module, "main", None)):
            continue
        try:
            module.main()
        except _Parsed as got:
            if "--preview" in got.parser._known_flags():
                found.add(info.name)
    return found


def _invocations(run: str, cli: str) -> list[str]:
    """Each run of `python3 -m dsl_course.<cli>` in a run block, as the text that decides
    its flags: the command itself (with its continuation lines), plus - when it passes an
    `args` array - every line before it that builds that array."""
    lines = run.splitlines()
    out = []
    for i, line in enumerate(lines):
        if not re.search(rf"-m dsl_course\.{cli}(\s|\"|$)", line):
            continue
        own = [line]
        j = i
        while own[-1].rstrip().endswith("\\") and j + 1 < len(lines):
            j += 1
            own.append(lines[j])
        text = "\n".join(own)
        if "${args[@]}" in text:
            text += "\n" + "\n".join(
                earlier
                for earlier in lines[:i]
                if re.search(r"(^|\W)args\+?=\(", earlier)
            )
        out.append(text)
    return out


def test_every_cli_previews_unless_told_otherwise(monkeypatch, capsys):
    seen: list[bool] = []
    monkeypatch.setattr(
        assign, "provision_all", lambda *a, dry_run, **k: seen.append(dry_run) or (0, 0)
    )
    monkeypatch.setattr(assign, "listing_by_name", lambda org: None)
    base = [
        "assign",
        "--course-org",
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
    assert f"{NOT_MIGRATED}: `--dry-run` is the old name of `--preview`" in (
        capsys.readouterr().err
    )


def test_every_rendered_run_of_a_previewing_cli_says_which_it_is(monkeypatch):
    # The CLI default is preview, so a rendered command that spelt neither flag would
    # preview for ever on a green run - a cron that released nothing, a roster push that
    # sent no codes. Checked per INVOCATION: each one spells `--preview` or `--no-preview`,
    # and every one in a job that runs unattended (no check-team gate: a cron, a
    # repository_dispatch, a config push) spells `--no-preview`.
    clis = _preview_clis(monkeypatch)
    assert {
        "scheduler",
        "assign",
        "enrol_codes",
        "sync_membership",
        "collect",
        "deploy",
        "teardown",
        "archive",
    } <= clis
    seen = 0
    for name, rendered in ALL_RENDERED.items():
        for job in (yaml.safe_load(rendered).get("jobs") or {}).values():
            unattended = "check-team" not in str(job.get("needs", ""))
            for step in job.get("steps") or []:
                run = str(step.get("run") or "")
                for cli in clis:
                    for text in _invocations(run, cli):
                        seen += 1
                        assert "--no-preview" in text or "--preview" in text, (
                            name,
                            cli,
                        )
                        if unattended and cli != "console":
                            assert "--no-preview" in text, (name, cli, "unattended")
    assert seen >= 15


@pytest.mark.parametrize("event", ["schedule", "repository_dispatch"])
def test_the_scheduler_acts_on_every_unattended_arrival(event):
    from test_renderers import _args_under_bash

    from dsl_course import workflows_render as w

    gate = "args=()\n" + w._SCHEDULED_PREVIEW_GATE
    for preview in ("", "true", "false"):
        got = _args_under_bash(gate, {"EVENT": event, "PREVIEW": preview})
        assert got == ["--no-preview"], (event, preview)
    press = {"EVENT": "workflow_dispatch"}
    assert _args_under_bash(gate, press | {"PREVIEW": ""}) == ["--preview"]
    assert _args_under_bash(gate, press | {"PREVIEW": "false"}) == ["--no-preview"]


def test_every_console_op_acts_only_when_asked_and_says_so():
    from dsl_course.ops.registry import command
    from dsl_course.ops.request import Request

    for op in REGISTRY.values():
        if not op.preview_flag:
            continue
        args = {
            "course_source_repo": "a1",
            "entry": "s5",
            "number": "1",
            "semester": "f2026",
        }
        real = command(op, Request(op.name, "prof", "C", "S", args, False))
        preview = command(op, Request(op.name, "prof", "C", "S", args, True))
        assert "--preview" in preview, op.name
        if op.name == "cohort.preview_automation":
            assert "--no-preview" not in real
        else:
            assert real[-1] == "--no-preview", op.name


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


# ------------------------------------------------------------------- kind (row type)


def test_a_row_kind_is_read_from_kind_and_the_old_type_key_is_refused():
    sched = schedule.parse(
        {
            "releases": {
                "lab-1": {"event_datetime": "2026-09-08T09:00", "kind": "lab"},
                "old-1": {"event_datetime": "2026-09-09T09:00", "type": "lab"},
            },
            "events": {
                "mid": {"event_datetime": "2026-11-03", "kind": "exam"},
                "old": {"event_datetime": "2026-11-04", "type": "exam"},
            },
        }
    )
    kinds = {r.label: r.kind for r in sched.releases}
    assert kinds == {"lab-1": "lab", "old-1": ""}
    assert {e.label: e.kind for e in sched.events} == {
        "mid": "exam",
        "old": "special_event",
    }
    assert sum(f.code == NOT_MIGRATED for f in sched.faults) == 2


def test_the_site_emits_kind_beside_the_pinned_themes_type():
    from datetime import date

    out = site._event_entry(
        schedule.Event("mid", "Mid", date(2026, 11, 3), kind="exam"), date(2026, 9, 1)
    )
    assert "kind: exam" in out and "type: exam" in out


def test_status_json_rows_say_kind():
    row = schemas.status_schema()["properties"]["releases"]["items"]["properties"]
    assert "kind" in row and "type" not in row


# --------------------------------------------- --course-org (--master-org, --source-org)


@pytest.mark.parametrize(
    "module, old",
    [(assign, "--master-org"), (collect, "--master-org"), (deploy, "--source-org")],
)
def test_the_course_org_flag_is_course_org_and_the_old_one_is_refused(
    monkeypatch, capsys, module, old
):
    # Every required flag given, so the refusal is for the old spelling and nothing else.
    base = ["--course-org", "C", "--course-source-repo", "r", "--semester-org", "S"]
    monkeypatch.setattr(sys, "argv", ["cli", *base, old, "C"])
    with pytest.raises(SystemExit):
        module.main()
    assert f"{NOT_MIGRATED}: `{old}` is the old name of `--course-org`" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "argv",
    [
        [
            "scaffold",
            "assignment",
            "--org",
            "O",
            "--number",
            "1",
            "--semester",
            "f2026",
            "--format",
            "py",
        ],
        ["scaffold", "materials", "--org", "O", "--tag", "f2026"],
    ],
)
def test_no_old_flag_is_prefix_matched_onto_a_new_one(monkeypatch, capsys, argv):
    from dsl_course import scaffold

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        scaffold.main()
    assert NOT_MIGRATED in capsys.readouterr().err


def test_status_writes_only_when_told_no_preview(monkeypatch, capsys):
    # `status --format` keeps its own meaning there; `--write` is now `--no-preview`.
    seen: list[tuple] = []
    monkeypatch.setattr(status, "refresh", lambda *a: seen.append(a) or 0)
    monkeypatch.setattr(sys, "argv", ["status", "--course-org", "C", "--no-preview"])
    assert status.main() == 0 and seen == [("C", None)]
    monkeypatch.setattr(sys, "argv", ["status", "--course-org", "C", "--write"])
    with pytest.raises(SystemExit):
        status.main()
    assert f"{NOT_MIGRATED}: `--write` is the old name of `--no-preview`" in (
        capsys.readouterr().err
    )


def test_a_semester_template_description_converges_off_its_old_ending(monkeypatch):
    from dsl_course import repos

    patched: list[str] = []
    monkeypatch.setattr(repos, "gh", lambda *a, **k: patched.append(a[-1]) or (0, ""))
    listing = [{"name": "a1", "description": "a1 - cohort assignment template"}]
    assert repos.converge_descriptions("S", listing, "semester").changed == 1
    assert patched == ["description=a1 - semester assignment template"]


def test_no_rendered_workflow_spells_an_old_course_org_flag_or_variable():
    for name, rendered in ALL_RENDERED.items():
        for old in ("--master-org", "--source-org", "MASTER_ORG", "SRC_ORG"):
            assert old not in rendered, (name, old)


# ------------------------------------------------ archive (teardown, freeze, close out)


def test_archive_is_the_documented_entry_point_and_runs_teardown(monkeypatch):
    from dsl_course import archive, teardown

    monkeypatch.setattr(teardown, "main", lambda: 7)
    assert archive.main() == 7
    assert REGISTRY["cohort.archive"].module == "archive"
    assert "python3 -m dsl_course.archive" in ALL_RENDERED["archive_semester"]


def test_an_open_notice_under_the_old_prefix_is_edited_not_duplicated(monkeypatch):
    from datetime import UTC, date, datetime

    from dsl_course import issues as issues_mod
    from dsl_course import scheduler, teardown

    when = date(2027, 2, 16)
    old = f"Cohort archives on {when}"
    assert teardown.is_archive_notice(old) and teardown.notice_date(old) == when
    open_issue = issues_mod.Issue(7, "body", False)
    looked: list[str] = []
    monkeypatch.setattr(
        scheduler.issues,
        "find_issue",
        lambda repo, title: (
            looked.append(title) or (open_issue if title == old else None)
        ),
    )
    written: list[str] = []
    monkeypatch.setattr(
        scheduler.issues,
        "upsert_issue",
        lambda repo, title, body, **k: written.append(title) or issues_mod.Upserted(0),
    )
    monkeypatch.setattr(scheduler.notify, "notify_semester_archiving", lambda *a: True)
    monkeypatch.setattr(scheduler.discovery, "central_ref_for", lambda org: "main")
    scheduler._archive_notice("C", "S", when, datetime(2027, 2, 5, tzinfo=UTC), False)
    assert looked == [f"Semester archives on {when}", old]
    assert written == [old]  # the old notice, edited in place
    closed: list[str] = []
    monkeypatch.setattr(scheduler.issues, "open_titles", lambda repo: {old})
    monkeypatch.setattr(
        scheduler.issues,
        "close_issues_titled",
        lambda repo, title, comment=None: closed.append(title) or 0,
    )
    scheduler._stale_archive_notices("S", teardown.archive_notice_title(when), False)
    assert closed == []  # the same date under the old prefix is not stale


def test_ds01s_all_cohorts_is_a_deprecated_alias_and_cohort_org_is_still_refused():
    import os
    import subprocess

    step = next(
        s
        for s in yaml.safe_load(ALL_RENDERED["sync_membership"])["jobs"]["sync-auto"][
            "steps"
        ]
        if "DISPATCH_ALL" in (s.get("env") or {})
    )
    assert "all_cohorts" in step["env"]["DISPATCH_ALL"]
    assert "all_cohorts" not in step["env"]["OLD_PAYLOAD"]
    run = step["run"]
    check = run[: run.index("# First, and never fatal")]
    env = {"PATH": os.environ["PATH"], "OLD_PAYLOAD": "", "DEPRECATED_ALL": "true"}
    out = subprocess.run(
        ["bash", "-c", check], env=env, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0 and "deprecated" in out.stdout
    env |= {"OLD_PAYLOAD": "Sem-f2026", "DEPRECATED_ALL": "false"}
    out = subprocess.run(
        ["bash", "-c", check], env=env, capture_output=True, text=True, check=False
    )
    assert out.returncode == 1 and "NOT_MIGRATED" in out.stdout


def test_semester_cards_never_come_from_the_old_people_shape(monkeypatch):
    from dsl_course import site_repo

    monkeypatch.setattr(site_repo, "_team_people", lambda org, team: [])
    page = site_repo.people_yaml("Sem", OLD_PEOPLE, edit_at="x", semester=True)
    assert "old" not in page and "declared in the" not in page
    course_page = site_repo.people_yaml(
        "Course", {"people": {"instructors": [{"name": "Prof"}]}}, edit_at="x"
    )
    assert "Prof" in course_page  # a COURSE file's `people:` block is its own shape
