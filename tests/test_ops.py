"""The Instructor Console's operations: the registry, the request parser, the public
outcome, and `console.main` end to end against stubbed target CLIs."""

from __future__ import annotations

import importlib
import json
import re
import sys

import pytest
from conftest import workflow_inputs, workflow_jobs

from dsl_course import console, deploy, grades, scheduler, status, workflows_render
from dsl_course.ops import outcome as outcome_mod
from dsl_course.ops import request as request_mod
from dsl_course.ops.outcome import Outcome, annotation
from dsl_course.ops.registry import (
    REGISTRY,
    Request,
    command,
)
from dsl_course.ops.registry import (
    workflow_inputs as op_inputs,
)
from dsl_course.ops.request import RequestError, parse_request
from dsl_course.schedule import Deploy, Release, Schedule

COURSE = "hertie-dsl-demo-course-e1234"
SEMESTER = "hertie-dsl-demo-f2026"

# contracts.md section 1, verbatim but for the actor placeholder.
CONTRACT_REQUEST = {
    "schema": "dsl.request/1",
    "op": "release.now",
    "actor": "prof",
    "course_org": COURSE,
    "semester_org": SEMESTER,
    "args": {"entry": "s5"},
    "preview": True,
    "client": "console/0.1",
}

CONTRACT_OPS = {
    "cohort.check",
    "cohort.preview_automation",
    "release.now",
    "release.early",
    "release.rerun",
    "release.adhoc",
    "release.propagate_back",
    "assignment.handout_now",
    "assignment.update_copies",
    "assignment.collect_now",
    "grades.return",
    "roster.send_codes",
    "site.update",
    "access.check",
    "cohort.archive",
    "course.publish_website",
    "assignment.derive_starter",
    "assignment.generate_syllabus",
    "materials.create",
    "assignment.create",
    "cohort.bootstrap",
    # Added after the contract's first list: Open team formation, shipped 23 Sep.
    "teams.open_window",
}


def _request(**over) -> dict:
    return {**CONTRACT_REQUEST, **over}


# ------------------------------------------------------------------ registry


def test_the_registry_is_every_dispatch_op_the_contract_lists():
    assert set(REGISTRY) == CONTRACT_OPS


@pytest.mark.parametrize("name", sorted(CONTRACT_OPS))
def test_every_op_targets_a_cli_with_a_main(name):
    op = REGISTRY[name]
    assert op.runs_as == "dispatch"
    assert op.scope in ("course", "cohort")
    assert callable(importlib.import_module(f"dsl_course.{op.module}").main)


def _run_flags(rendered: str, module: str) -> set[str]:
    """Every `--flag` the manual job's step spells around `python3 -m dsl_course.<module>`."""
    for job in workflow_jobs(rendered).values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if f"-m dsl_course.{module}" in run:
                return set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", run))
    raise AssertionError(f"no step runs dsl_course.{module}")


def _all_flags(name: str, args: dict, semester: str | None = SEMESTER) -> set[str]:
    """The flags the registry can spell for an op: every optional arg on, both gates."""
    op = REGISTRY[name]
    flags = set()
    for preview in (True, False):
        req = Request(name, "prof", COURSE, semester, args, preview)
        flags |= {t for t in command(op, req) if t.startswith("--")}
    return flags


@pytest.mark.parametrize(
    ("name", "rendered", "args"),
    [
        (
            "cohort.archive",
            workflows_render.render_archive_semester([SEMESTER]),
            {"force": True},
        ),
        (
            "assignment.update_copies",
            workflows_render.render_patch_assignment(
                [SEMESTER], ["assignment-1-f2026"]
            ),
            {
                "course_source_repo": "assignment-1-f2026",
                "path": "a.ipynb",
                "slug": "a1",
                "overwrite": True,
            },
        ),
        (
            "assignment.collect_now",
            workflows_render.render_collect_submissions(
                [SEMESTER], ["assignment-1-f2026"]
            ),
            {"course_source_repo": "assignment-1-f2026", "slug": "a1"},
        ),
    ],
)
def test_argv_spells_the_flags_the_seeded_workflow_spells(name, rendered, args):
    op = REGISTRY[name]
    assert _all_flags(name, args) == _run_flags(rendered, op.module)


def test_a_workflow_op_dispatches_inputs_its_workflow_declares():
    op = REGISTRY["assignment.collect_now"]
    declared = workflow_inputs(workflows_render.render_collect_submissions([SEMESTER]))
    assert set(op.inputs) <= set(declared)
    req = Request(
        op.name,
        "prof",
        COURSE,
        SEMESTER,
        {"course_source_repo": "assignment-1-f2026"},
        True,
    )
    assert op_inputs(op, command(op, req)) == {
        "semester_org": SEMESTER,
        "course_source_repo": "assignment-1-f2026",
        "dry_run": "true",
    }


def test_a_real_run_of_a_default_on_dry_run_cli_says_no_dry_run():
    op = REGISTRY["grades.return"]
    real = command(op, Request(op.name, "prof", COURSE, SEMESTER, {}, False))
    assert real[-1] == "--no-dry-run" and "--dry-run" not in real


# ------------------------------------------------------------------ request


def test_the_contract_example_parses():
    req = parse_request(json.dumps(CONTRACT_REQUEST))
    assert (req.op, req.semester_org, req.args, req.preview) == (
        "release.now",
        SEMESTER,
        {"entry": "s5"},
        True,
    )


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (
            _request(
                op="course.publish_website",
                semester_org=None,
                args={"source_repo": "m", "readings_mode": "all"},
            ),
            "BAD_ARGS",
        ),
        ({k: v for k, v in CONTRACT_REQUEST.items() if k != "actor"}, "BAD_REQUEST"),
        (_request(op="site.update", args={}), "NO_PREVIEW"),
        (_request(op="release.later"), "UNKNOWN_OP"),
        (_request(args={"entry": "s5", "surprise": 1}), "BAD_ARGS"),
        (_request(args={"entry": "--no-dry-run"}), "BAD_ARGS"),
        (_request(semester_org=None), "BAD_REQUEST"),
    ],
)
def test_a_bad_request_is_refused_with_its_code(raw, code):
    raw = {k: v for k, v in raw.items() if v is not None}
    with pytest.raises(RequestError) as exc:
        parse_request(json.dumps(raw))
    assert exc.value.code == code


def test_not_json_is_refused():
    with pytest.raises(RequestError) as exc:
        parse_request("{nope")
    assert exc.value.code == "BAD_REQUEST"


def _teams(members: dict[tuple[str, str], set[str]]):
    return lambda org, team: members.get((org, team), set())


@pytest.fixture(autouse=True)
def _semester_is_registered(monkeypatch):
    monkeypatch.setattr(request_mod, "discover_semesters", lambda org: [SEMESTER])


def test_a_semester_of_another_course_is_refused(monkeypatch):
    req = parse_request(json.dumps(_request()))
    monkeypatch.setattr(request_mod, "discover_semesters", lambda org: ["other-f2026"])
    monkeypatch.setattr(
        request_mod, "get_team_members", _teams({(COURSE, "course-admin"): {"prof"}})
    )
    assert "not a semester of" in request_mod.check_access(req)


def test_access_needs_the_semester_instructors_team_or_course_admin(monkeypatch):
    req = parse_request(json.dumps(_request()))
    monkeypatch.setattr(
        request_mod, "get_team_members", _teams({(SEMESTER, "instructors"): {"Prof"}})
    )
    assert request_mod.check_access(req) is None
    monkeypatch.setattr(request_mod, "get_team_members", _teams({}))
    assert "instructors" in request_mod.check_access(req)
    monkeypatch.setattr(
        request_mod, "get_team_members", _teams({(COURSE, "course-admin"): {"prof"}})
    )
    assert request_mod.check_access(req) is None


def test_a_course_op_accepts_any_term_instructors_team(monkeypatch):
    raw = _request(
        op="assignment.derive_starter",
        preview=False,
        args={"course_source_repo": "assignment-1-f2026"},
    )
    del raw["semester_org"]
    req = parse_request(json.dumps(raw))
    monkeypatch.setattr(
        request_mod,
        "list_teams",
        lambda org: {"instructors-f2026": "", "course-admin": ""},
    )
    monkeypatch.setattr(
        request_mod,
        "get_team_members",
        _teams({(COURSE, "instructors-f2026"): {"prof"}}),
    )
    assert request_mod.check_access(req) is None


def test_bootstrap_needs_course_admin(monkeypatch):
    req = parse_request(
        json.dumps(_request(op="cohort.bootstrap", preview=False, args={}))
    )
    monkeypatch.setattr(
        request_mod, "get_team_members", _teams({(SEMESTER, "instructors"): {"prof"}})
    )
    assert "course-admin" in request_mod.check_access(req)


# ------------------------------------------------------------------ outcome


def test_the_annotation_names_nobody():
    out = Outcome(
        op="assignment.update_copies",
        actor="prof",
        preview=False,
        conclusion="done",
        summary="Patched assignment-3-octocat and grades-octocat; octocat pulled. 10% late.",
        reasons=[
            {
                "code": "SKIPPED",
                "text": "assignment-3-octocat kept its own file",
                "fix": {"repo": f"{SEMESTER}/assignment-3-octocat"},
            }
        ],
        people=[{"handle": "octocat", "text": "No repo: not joined yet."}],
    )
    line = annotation(out)
    assert line.startswith("::notice title=dsl-outcome::")
    assert "octocat" not in line.lower()
    assert "10%25 late" in line
    body = json.loads(line.split("::", 2)[2].replace("%25", "%"))
    assert "people" not in body
    assert body["actor"] == "prof"
    assert (
        "assignment-3-<handle>" in body["summary"]
        and "grades-<handle>" in body["summary"]
    )


def _body(line: str) -> dict:
    message = line.split("::", 2)[2]
    for code, char in (("%0A", "\n"), ("%0D", "\r"), ("%25", "%")):
        message = message.replace(code, char)
    return json.loads(message)


def test_a_huge_block_is_cut_to_fit_the_annotation_and_still_parses():
    out = Outcome(
        op="assignment.generate_syllabus",
        actor="prof",
        preview=True,
        conclusion="previewed",
        summary="Built the session list: 12 sessions; nothing was written.",
        details=["solution/a.py -> a.py"],
        block="### Session 1: 100% theory\n" * 8000,  # ~200 KB, % and newlines escape
    )
    line = annotation(out)
    message = line.split("::", 2)[2]
    assert len(message.encode()) <= outcome_mod.ANNOTATION_CAP
    body = _body(line)
    assert body["block"].endswith(outcome_mod.TRUNCATED)
    assert body["block"].startswith("### Session 1: 100% theory")
    assert body["details"] == ["solution/a.py -> a.py"]
    assert body["summary"] == out.summary


def test_details_go_once_the_block_is_empty():
    out = Outcome(
        op="assignment.derive_starter",
        actor="prof",
        preview=True,
        conclusion="previewed",
        summary="x",
        details=[f"solution/{n:05}.py -> {n:05}.py" for n in range(5000)],
    )
    line = annotation(out)
    assert len(line.split("::", 2)[2].encode()) <= outcome_mod.ANNOTATION_CAP
    body = _body(line)
    assert body["block"] == outcome_mod.TRUNCATED
    assert 0 < len(body["details"]) < 5000
    assert body["details"][0] == "solution/00000.py -> 00000.py"


def test_a_small_outcome_is_not_touched():
    out = Outcome(op="x", actor="p", preview=False, conclusion="done", summary="y")
    assert _body(annotation(out))["block"] == ""


def test_redaction_keeps_templates_and_the_shared_drop_box():
    text = "assignment-3-f2026 and assignment-3-submissions"
    assert outcome_mod.redact(text) == text


# ------------------------------------------------------------------ console.main end to end


@pytest.fixture
def engine(monkeypatch):
    """A console whose world is stubbed at the names it imports: the bot login, the team
    listings, the private write. Returns what was written."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(console, "acting_login", lambda: "prof")
    monkeypatch.setattr(
        request_mod, "get_team_members", _teams({(COURSE, "course-admin"): {"prof"}})
    )
    monkeypatch.setattr(status, "write_after_op", lambda request: None)
    writes = []
    monkeypatch.setattr(
        outcome_mod, "put_file", lambda *a, **k: writes.append((a, k)) or True
    )
    return writes


def _main(monkeypatch, capsys, raw: dict) -> tuple[int, dict | None, str]:
    monkeypatch.setattr(
        sys, "argv", ["dsl_course.console", "--request", json.dumps(raw)]
    )
    rc = console.main()
    out = capsys.readouterr().out
    notes = [
        line
        for line in out.splitlines()
        if line.startswith("::notice title=dsl-outcome::")
    ]
    body = (
        json.loads(notes[-1].split("::", 2)[2].replace("%0A", "\n").replace("%25", "%"))
        if notes
        else None
    )
    return rc, body, out


def test_a_return_marks_run_end_to_end(monkeypatch, capsys, engine):
    seen = {}

    def fake_main():
        seen["argv"] = sys.argv[1:]
        return grades.return_summary(
            {"gradebooks": 3, "emails": 3, "held": 0}, 3, dry_run=False
        )

    monkeypatch.setattr(grades, "main", fake_main)
    rc, body, _ = _main(
        monkeypatch,
        capsys,
        _request(op="grades.return", args={"notify": False}, preview=False),
    )
    assert rc == 0
    assert seen["argv"] == [
        "distribute",
        "--semester-org",
        SEMESTER,
        "--no-notify",
        "--no-dry-run",
    ]
    assert body["conclusion"] == "done"
    assert body["summary"] == "Marks returned: 3 marks repos updated, 3 emails sent."
    assert body["counts"] == {"gradebooks": 3, "emails": 3, "held": 0}
    (org, repo, path, content, _msg), _ = engine[0]
    assert (org, repo, path) == (
        SEMESTER,
        "classroom-config",
        ".dsl/outcomes/grades.return.json",
    )
    assert json.loads(content)["schema"] == "dsl.outcome/1"


def test_a_failed_target_is_a_conclusion_not_a_red_run(monkeypatch, capsys, engine):
    monkeypatch.setattr(grades, "main", lambda: sys.exit(2))
    rc, body, _ = _main(
        monkeypatch, capsys, _request(op="grades.return", args={}, preview=True)
    )
    assert rc == 0 and body["conclusion"] == "failed"


def test_a_crashed_target_breaks_the_run_without_a_traceback(
    monkeypatch, capsys, engine
):
    def boom():
        raise KeyError("grades-octocat")

    monkeypatch.setattr(grades, "main", boom)
    rc, body, out = _main(
        monkeypatch, capsys, _request(op="grades.return", args={}, preview=True)
    )
    assert rc == 1 and body["conclusion"] == "failed"
    assert "Traceback" not in out and "octocat" not in out


def test_a_preview_on_an_op_without_one_is_refused_and_green(
    monkeypatch, capsys, engine
):
    rc, body, _ = _main(monkeypatch, capsys, _request(op="site.update", args={}))
    assert rc == 0
    assert body["reasons"][0]["code"] == "NO_PREVIEW"
    assert engine == []


def test_inside_actions_the_request_must_speak_for_the_actor(
    monkeypatch, capsys, engine
):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_ACTOR", "someone-else")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", COURSE)
    rc, body, _ = _main(monkeypatch, capsys, _request(op="grades.return", args={}))
    assert rc == 0
    assert body["reasons"][0]["code"] == "ACTOR_MISMATCH"
    assert engine == []


def test_no_token_breaks_the_run(monkeypatch, capsys, engine):
    monkeypatch.setattr(console, "acting_login", lambda: None)
    rc, body, _ = _main(monkeypatch, capsys, _request(op="grades.return", args={}))
    assert rc == 1 and body is None


def test_a_named_entry_is_released_from_its_schedule_row(monkeypatch, capsys, engine):
    sched = Schedule(
        releases=[
            Release(
                label="s5",
                when=None,
                deploy=[
                    Deploy("course-materials-f2026", "lectures/05"),
                    Deploy(
                        "course-materials-f2026", "labs/05", semester_dest_path="labs/5"
                    ),
                ],
            )
        ]
    )
    monkeypatch.setattr(console.schedule, "load", lambda org: sched)
    seen = []

    def fake_main():
        seen.append(sys.argv[1:])
        return 0

    monkeypatch.setattr(deploy, "main", fake_main)
    rc, body, _ = _main(monkeypatch, capsys, CONTRACT_REQUEST)
    assert rc == 0 and body["conclusion"] == "previewed"
    argv = seen[0]
    assert argv[argv.index("--course-source-path") + 1] == "lectures/05,labs/05"
    assert argv[argv.index("--semester-dest-path") + 1] == "lectures/05,labs/5"
    assert argv[-1] == "--dry-run"


def test_an_unknown_entry_is_a_reason(monkeypatch, capsys, engine):
    monkeypatch.setattr(console.schedule, "load", lambda org: Schedule())
    rc, body, _ = _main(monkeypatch, capsys, CONTRACT_REQUEST)
    assert rc == 0 and body["reasons"][0]["code"] == "ENTRY_NOT_FOUND"


def test_scheduler_decisions_become_reasons(monkeypatch, capsys, engine):
    def fake_main():
        decision = scheduler.Decision(
            "s5", "SOURCE_MISSING", "Folder lectures/05 was not found."
        )
        return scheduler.preview_summary([], [decision])

    monkeypatch.setattr(scheduler, "main", fake_main)
    rc, body, _ = _main(
        monkeypatch, capsys, _request(op="cohort.preview_automation", args={})
    )
    assert rc == 0 and body["conclusion"] == "previewed"
    assert body["reasons"] == [
        {
            "code": "SOURCE_MISSING",
            "text": "s5 not released: Folder lectures/05 was not found.",
        }
    ]


def test_collect_now_starts_its_own_workflow(monkeypatch, capsys, engine):
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return 0, json.dumps(
            {"workflow_run_id": 7, "html_url": "https://github.com/x/runs/7"}
        )

    monkeypatch.setattr(console, "gh", fake_gh)
    raw = _request(
        op="assignment.collect_now",
        args={"course_source_repo": "assignment-1-f2026"},
        preview=False,
    )
    rc, body, _ = _main(monkeypatch, capsys, raw)
    assert rc == 0 and body["conclusion"] == "done"
    assert "https://github.com/x/runs/7" in body["summary"]
    (args,) = calls
    assert (
        f"repos/{COURSE}/.github/actions/workflows/collect-submissions.yml/dispatches"
        in args
    )
    assert f"inputs[semester_org]={SEMESTER}" in args
    assert not any("dry_run" in a for a in args)


def test_a_refusal_for_another_courses_semester_writes_nowhere(
    monkeypatch, capsys, engine
):
    monkeypatch.setattr(request_mod, "discover_semesters", lambda org: ["other-f2026"])
    rc, body, _ = _main(monkeypatch, capsys, _request(op="grades.return", args={}))
    assert rc == 0
    assert body["reasons"][0]["code"] == "NOT_ALLOWED"
    assert engine == []


def test_a_real_archive_is_not_a_broken_run(monkeypatch, capsys, engine):
    from dsl_course import teardown

    monkeypatch.setattr(teardown, "main", lambda: 0)
    monkeypatch.setattr(
        outcome_mod, "put_file", lambda *a, **k: False
    )  # classroom-config is read-only now
    rc, body, _ = _main(
        monkeypatch, capsys, _request(op="cohort.archive", args={}, preview=False)
    )
    assert rc == 0 and body["conclusion"] == "done"


def test_a_previewed_collect_says_previewed(monkeypatch, capsys, engine):
    monkeypatch.setattr(console, "gh", lambda *a, **k: (0, "{}"))
    raw = _request(
        op="assignment.collect_now",
        args={"course_source_repo": "assignment-1-f2026"},
        preview=True,
    )
    rc, body, _ = _main(monkeypatch, capsys, raw)
    assert rc == 0 and body["conclusion"] == "previewed"


def test_a_trailing_newline_does_not_pass_a_pattern():
    with pytest.raises(RequestError) as exc:
        parse_request(json.dumps(_request(actor="prof\n")))
    assert exc.value.code == "BAD_REQUEST"
    with pytest.raises(RequestError) as exc:
        parse_request(json.dumps(_request(args={"entry": "s5\n"})))
    assert exc.value.code == "BAD_ARGS"


def test_a_failed_refresh_after_a_new_template_is_a_reason_not_a_failure(
    monkeypatch, capsys, engine
):
    from dsl_course import scaffold, seed

    monkeypatch.setattr(scaffold, "main", lambda: 0)
    monkeypatch.setattr(seed, "main", lambda: 1)
    raw = _request(
        op="assignment.create", args={"number": "2", "tag": "f2026"}, preview=False
    )
    del raw["semester_org"]
    rc, body, _ = _main(monkeypatch, capsys, raw)
    assert rc == 0 and body["conclusion"] == "done"
    assert [r["code"] for r in body["reasons"]] == ["REFRESH_FAILED"]
