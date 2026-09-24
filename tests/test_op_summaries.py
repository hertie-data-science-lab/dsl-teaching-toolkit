"""What each console op says it did: the `log.Summary` a CLI's `main` hands back, and how
the Console run turns it into the outcome's `summary`, `counts`, `reasons` and
`conclusion`.

Every sentence here lands in a public annotation, so the tests also hold them to counts:
no handle, no address, no `<slug>-<handle>` repo.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from dsl_course import (
    assign,
    console,
    deploy,
    derive,
    enrol_codes,
    gh_teams,
    grades,
    propagate,
    scheduler,
    site_repo,
    status,
    syllabus,
    sync_membership,
    team_formation,
    teardown,
)
from dsl_course.log import Summary
from dsl_course.ops import outcome as outcome_mod
from dsl_course.ops import request as request_mod
from dsl_course.ops.registry import REGISTRY, command
from dsl_course.schedule import Release, Schedule

COURSE = "hertie-dsl-demo-course-e1234"
SEMESTER = "hertie-dsl-demo-f2026"
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)


# ------------------------------------------------------------------ the carrier


def test_a_summary_is_an_exit_code():
    s = Summary("Done it.", {"files": 2}, code=1)
    assert s == 1 and int(s) == 1 and s + 1 == 2
    assert s.text == "Done it." and s.counts == {"files": 2}
    assert Summary("Nothing.") == 0 and not Summary("Nothing.")


def test_sys_exit_takes_a_summary_as_its_code():
    with pytest.raises(SystemExit) as exc:
        sys.exit(Summary("x", code=3))
    assert exc.value.code == 3


def test_run_cli_hands_back_the_summary_main_returned(monkeypatch):
    monkeypatch.setattr(deploy, "main", lambda: Summary("Released 1 item.", code=0))
    rc, summary, crashed = console.run_cli("deploy", [])
    assert (rc, crashed) == (0, False)
    assert summary.text == "Released 1 item."


def test_run_cli_with_a_bare_exit_code_has_no_summary(monkeypatch):
    monkeypatch.setattr(deploy, "main", lambda: 0)
    assert console.run_cli("deploy", []) == (0, None, False)


def test_combine_adds_counts_and_joins_different_sentences():
    text, counts, reasons, details, block, conclusion = console.combine(
        [
            Summary("Released 2 items from a to materials.", {"items": 2}),
            Summary(
                "Released 1 item from b to materials.",
                {"items": 1},
                [{"code": "X", "text": "y"}],
                details=["b/x.md"],
                block="## Sessions",
            ),
        ]
    )
    assert text == (
        "Released 2 items from a to materials; released 1 item from b to materials."
    )
    assert counts == {"items": 3}
    assert reasons == [{"code": "X", "text": "y"}]
    assert details == ["b/x.md"]
    assert block == "## Sessions"
    assert conclusion is None


def test_combine_keeps_a_conclusion_only_when_every_call_agrees():
    quiet = Summary("Nothing new.", conclusion="nothing_to_do")
    busy = Summary("Released 1 item.")
    assert console.combine([quiet, quiet])[5] == "nothing_to_do"
    assert console.combine([quiet, busy])[5] is None


# ------------------------------------------------------------------ end to end


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(console, "acting_login", lambda: "prof")
    monkeypatch.setattr(
        request_mod,
        "get_team_members",
        lambda org, team: {"prof"} if team == "course-admin" else set(),
    )
    monkeypatch.setattr(request_mod, "discover_semesters", lambda org: [SEMESTER])
    hooked = []
    monkeypatch.setattr(status, "write_after_op", hooked.append)
    monkeypatch.setattr(outcome_mod, "put_file", lambda *a, **k: True)
    return hooked


def _run(
    monkeypatch,
    capsys,
    op: str,
    preview: bool = False,
    args: dict | None = None,
    semester: str | None = SEMESTER,
) -> dict:
    raw = {
        "schema": "dsl.request/1",
        "op": op,
        "actor": "prof",
        "course_org": COURSE,
        "semester_org": semester,
        "args": args or {},
        "preview": preview,
    }
    if semester is None:
        del raw["semester_org"]
    monkeypatch.setattr(
        sys, "argv", ["dsl_course.console", "--request", json.dumps(raw)]
    )
    assert console.main() == 0
    (note,) = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("::notice title=dsl-outcome::")
    ]
    return json.loads(note.split("::", 2)[2].replace("%25", "%"))


def test_a_summary_becomes_the_outcome(monkeypatch, capsys, engine):
    monkeypatch.setattr(
        sync_membership,
        "main",
        lambda: Summary("Access checked: 2 team memberships changed.", {"a": 2}),
    )
    body = _run(monkeypatch, capsys, "access.check")
    assert body["conclusion"] == "done"
    assert body["summary"] == "Access checked: 2 team memberships changed."
    assert body["counts"] == {"a": 2}


def test_a_refused_derive_carries_its_reasons_and_file_list(
    monkeypatch, capsys, engine
):
    monkeypatch.setattr(
        derive,
        "main",
        lambda: Summary(
            "Would derive 1 file onto main; 1 file could not be derived.",
            {"files": 1, "refused": 1},
            [{"code": "NO_SOLUTION_REGION", "text": "solution/solution.py has no ..."}],
            code=1,
            details=["solution/starter.py -> starter.py: 1 region(s)"],
        ),
    )
    body = _run(
        monkeypatch,
        capsys,
        "assignment.derive_starter",
        preview=True,
        args={"course_source_repo": "assignment-1-f2026"},
        semester=None,
    )
    assert body["conclusion"] == "failed"
    assert [r["code"] for r in body["reasons"]] == ["NO_SOLUTION_REGION"]
    assert body["details"] == ["solution/starter.py -> starter.py: 1 region(s)"]


def test_the_session_list_reaches_the_outcome(monkeypatch, capsys, engine):
    monkeypatch.setattr(
        syllabus,
        "main",
        lambda: Summary("Built the session list: 1 session.", block="## Sessions\n"),
    )
    body = _run(
        monkeypatch,
        capsys,
        "assignment.generate_syllabus",
        preview=True,
        args={"course_source_repo": "course-materials-f2026"},
    )
    assert body["conclusion"] == "previewed"
    assert body["block"] == "## Sessions\n"


def test_a_summary_can_say_a_run_had_nothing_to_do(monkeypatch, capsys, engine):
    monkeypatch.setattr(
        sync_membership,
        "main",
        lambda: Summary("Already up to date.", conclusion="nothing_to_do"),
    )
    body = _run(monkeypatch, capsys, "access.check")
    assert body["conclusion"] == "nothing_to_do"


def test_a_bare_exit_code_gets_the_ops_own_sentence(monkeypatch, capsys, engine):
    monkeypatch.setattr(sync_membership, "main", lambda: 0)
    body = _run(monkeypatch, capsys, "access.check")
    assert body["summary"] == REGISTRY["access.check"].done_text


def test_the_status_hook_is_handed_the_request_as_a_dict(monkeypatch, capsys, engine):
    # The demo's first run: `write_after_op(request)` did `request.get(...)` on the
    # Request dataclass and every Console run logged "could not refresh status.json".
    monkeypatch.setattr(sync_membership, "main", lambda: 0)
    _run(monkeypatch, capsys, "access.check")
    (request,) = engine
    assert isinstance(request, dict)
    assert (request["course_org"], request["semester_org"]) == (COURSE, SEMESTER)


def test_every_real_op_has_a_sentence_to_fall_back_on():
    missing = [
        name
        for name, op in REGISTRY.items()
        if op.workflow is None and op.name != "cohort.preview_automation"
        if not op.done_text
    ]
    assert missing == []


def test_semester_check_writes_status_json():
    assert (
        REGISTRY["cohort.check"].argv(
            request_mod.parse_request(
                json.dumps(
                    {
                        "schema": "dsl.request/1",
                        "op": "cohort.check",
                        "actor": "prof",
                        "course_org": COURSE,
                        "semester_org": SEMESTER,
                        "args": {},
                        "preview": False,
                    }
                )
            )
        )[-1]
        == "--write"
    )


# ------------------------------------------------------------------ per CLI


def test_status_refresh_counts_problems_and_this_week():
    doc = {"semester": {}, "problems": [{}, {}], "this_week": [{}]}
    s = status.refreshed(doc)
    assert s.text == "Status refreshed: 2 problems, 1 item this week."
    assert s.counts == {"problems": 2, "this_week": 1}
    assert (
        status.refreshed({"problems": []}).text
        == "Course status refreshed: 0 problems."
    )


def test_status_write_returns_the_summary(monkeypatch):
    doc = {
        "course": {"semesters": [SEMESTER]},
        "semester": {"live": True},
        "problems": [{}],
        "this_week": [],
    }
    monkeypatch.setattr(status, "_document", lambda course, semester: doc)
    monkeypatch.setattr(status, "put_file", lambda *a, **k: True)
    out = status.write(COURSE, SEMESTER)
    assert out == 0
    assert out.text == "Status refreshed: 1 problem, 0 items this week."


def test_the_automation_preview_says_what_would_go_out_and_why_not():
    due = [
        Release(label="s4", when=None),
        Release(label="s5", when=None),
        Release(label="a2-handout", when=None, assignment_slug="assignment-2"),
    ]
    decisions = [
        scheduler.Decision("s5", "SOURCE_MISSING", "Folder lectures/05 is not found."),
        scheduler.Decision("assignment-2", "TEAMS_INCOMPLETE", "No teams yet."),
    ]
    s = scheduler.preview_summary(due, decisions)
    assert s.text == (
        "Automation would release 1 of 3 due entries now; 2 reasons why something "
        "would not go out."
    )
    assert s.counts == {"due": 3, "would_release": 1, "held": 2}
    assert s.reasons[0] == {
        "code": "SOURCE_MISSING",
        "text": "s5 not released: Folder lectures/05 is not found.",
    }


def test_the_automation_preview_with_nothing_due():
    s = scheduler.preview_summary([], [])
    assert s.text == "Automation has nothing due in this semester right now."
    assert s.reasons == []


class _SiteGit:
    def __init__(self, changed: list[str], commit_rc: int = 0):
        self.changed, self.commit_rc = changed, commit_rc

    def __call__(self, *args):
        if "diff" in args:
            return 0, "\n".join(self.changed)
        if "commit" in args:
            return self.commit_rc, ""
        return 0, ""


def _site(monkeypatch, fake_git) -> Summary:
    monkeypatch.setattr(site_repo, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(site_repo, "repo_is_archived", lambda org, name: False)
    monkeypatch.setattr(
        site_repo, "clone", lambda org, repo, wd: Path(wd).mkdir(parents=True) or True
    )
    monkeypatch.setattr(site_repo, "git", fake_git)
    monkeypatch.setattr(site_repo, "_overwritten_edits", lambda wd: {})

    def build(_wd):
        return site_repo.SitePlan(
            config={}, collections={}, commit="site: sync", title="Student site"
        )

    return site_repo.sync_site_repo(SEMESTER, build)


def test_a_site_update_counts_the_pages_it_changed(monkeypatch):
    out = _site(monkeypatch, _SiteGit(["_lectures/s1.md", "_data/nav.yml"]))
    assert out == 0
    assert out.text == "Student site updated: 2 pages changed."
    assert out.counts == {"pages": 2}


def test_a_site_already_up_to_date_had_nothing_to_do(monkeypatch):
    out = _site(monkeypatch, _SiteGit([], commit_rc=1))
    assert out == 0
    assert out.text == "Student site already up to date."
    assert out.conclusion == "nothing_to_do"


def test_reconcile_tallies_the_membership_changes(monkeypatch):
    monkeypatch.setattr(gh_teams, "get_team_members", lambda org, team: {"old", "bot"})
    monkeypatch.setattr(gh_teams, "add_team_member", lambda *a: True)
    monkeypatch.setattr(gh_teams, "remove_team_member", lambda *a: True)
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: {"bot"})
    monkeypatch.setattr(gh_teams, "acting_login", lambda: "bot")
    gh_teams.reset_membership_changes()
    assert gh_teams.reconcile_team_members(SEMESTER, "t", {"new1", "new2", "bot"}) == 0
    assert gh_teams.membership_changes() == {"added": 2, "removed": 1}
    gh_teams.reset_membership_changes()
    assert gh_teams.membership_changes() == {"added": 0, "removed": 0}


def test_the_access_check_sentence():
    assert (
        sync_membership.access_summary({"added": 2, "removed": 1}, False).text
        == "Access checked: 3 team memberships changed."
    )
    assert (
        sync_membership.access_summary({"added": 1, "removed": 0}, True).text
        == "Access checked: 1 team membership would change."
    )
    assert (
        sync_membership.access_summary({"added": 0, "removed": 0}, False).text
        == "Access checked: nothing needed changing."
    )


def test_the_new_codes_sentence_counts_and_never_names():
    sent = enrol_codes.new_codes_summary(
        {"students": 3, "skipped": 1, "sent": 3}, False, 0
    )
    assert sent.text == (
        "New codes sent to 3 students who have not joined; their old codes no longer "
        "work. 1 row skipped for an unusable email address."
    )
    preview = enrol_codes.new_codes_summary(
        {"students": 2, "skipped": 0, "sent": 0},
        True,
        0,
    )
    assert preview.text == "2 students who have not joined would get new codes."
    none = enrol_codes.new_codes_summary(
        {"students": 0, "skipped": 0, "sent": 0},
        False,
        0,
    )
    assert none.conclusion == "nothing_to_do"
    # A failed send keeps its bare exit code: the failure fallback speaks for it.
    assert not isinstance(
        enrol_codes.new_codes_summary(
            {"students": 2, "skipped": 0, "sent": 1},
            False,
            1,
        ),
        Summary,
    )


def _deploy_main(monkeypatch, *extra) -> Summary:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy",
            "--source-org",
            COURSE,
            "--course-source-repo",
            "course-materials-f2026",
            "--semester-org",
            SEMESTER,
            "--course-source-path",
            "lectures/05,labs/05",
            *extra,
        ],
    )
    return deploy.main()


def test_a_release_preview_counts_what_would_go(monkeypatch):
    out = _deploy_main(monkeypatch, "--dry-run")
    assert out == 0
    assert out.text == (
        "Preview: 2 items from course-materials-f2026 would be released to materials."
    )


def test_a_release_says_what_it_released(monkeypatch):
    monkeypatch.setattr(deploy, "deploy_many", lambda *a, **k: (0, True))
    out = _deploy_main(monkeypatch)
    assert out.text == "Released 2 items from course-materials-f2026 to materials."
    assert out.counts == {"items": 2}


def test_a_release_with_nothing_new_had_nothing_to_do(monkeypatch):
    monkeypatch.setattr(deploy, "deploy_many", lambda *a, **k: (0, False))
    out = _deploy_main(monkeypatch)
    assert out.conclusion == "nothing_to_do"
    assert out.text == (
        "Nothing new to release: 2 items from course-materials-f2026 are already in "
        "materials."
    )


def test_the_handout_sentence():
    out = assign.handout_summary(
        "assignment-2", {"ok": 3, "skipped": 5, "failed-create": 1}, True, True, 1
    )
    assert out == 1
    assert out.text == (
        "Handed out assignment-2: 3 new copies, 5 already out, 1 could not be handed out."
    )
    quiet = assign.handout_summary("assignment-2", {"skipped": 8}, False, True, 0)
    assert quiet.conclusion == "nothing_to_do"
    assert quiet.text == (
        "assignment-2 was already handed out: 8 copies in place, nothing new to create."
    )


def test_the_return_marks_sentence():
    counts = {"gradebooks": 3, "emails": 0, "held": 2, "unknown": 0, "failed": 0}
    assert grades.return_summary(counts, 3, dry_run=True).text == (
        "Preview: 3 marks repos would be updated and 3 emails sent; 2 marks held until "
        "the sheet is fixed."
    )
    real = dict(counts, emails=1, held=0)
    assert grades.return_summary(real, 1, dry_run=False).text == (
        "Marks returned: 3 marks repos updated, 1 email sent."
    )
    none = dict(counts, gradebooks=0, held=0)
    quiet = grades.return_summary(none, 0, dry_run=False)
    assert quiet.text == "No new marks to return."
    assert quiet.conclusion == "nothing_to_do"


def test_the_keep_for_future_terms_sentence():
    done = propagate.Propagated(0, ("https://github.com/x/pull/1",), (), 4)
    assert propagate.propagate_summary(done, False).text == (
        "Kept for future terms: 1 pull request opened on the course's materials."
    )
    quiet = propagate.propagate_summary(propagate.Propagated(checked=4), False)
    assert quiet.conclusion == "nothing_to_do"
    preview = propagate.propagate_summary(propagate.Propagated(checked=4), True)
    assert preview.text == (
        "Preview: 4 released items would be checked for edits to keep for future terms."
    )


def test_archiving_an_archived_semester_had_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        teardown,
        "list_org_repos",
        lambda org: [{"name": "classroom-config", "archived": True}],
    )
    out = teardown.close_out(COURSE, SEMESTER, dry_run=False)
    assert out == 0 and out.conclusion == "nothing_to_do"
    assert out.text == "This semester is already archived."


# ------------------------------------------------------------------ teams.open_window


def _open_window_request(preview: bool) -> request_mod.Request:
    return request_mod.parse_request(
        json.dumps(
            {
                "schema": "dsl.request/1",
                "op": "teams.open_window",
                "actor": "prof",
                "course_org": COURSE,
                "semester_org": SEMESTER,
                "args": {"assignment": "assignment-3-project"},
                "preview": preview,
            }
        )
    )


def test_open_window_spells_the_open_team_formation_flags():
    op = REGISTRY["teams.open_window"]
    assert (op.scope, op.required_team, op.via) == ("cohort", "instructors", "inline")
    base = [
        "--course-org",
        COURSE,
        "--semester-org",
        SEMESTER,
        "--assignment",
        "assignment-3-project",
    ]
    assert command(op, _open_window_request(True)) == [*base, "--dry-run"]
    assert command(op, _open_window_request(False)) == [*base, "--no-dry-run"]


def test_open_window_needs_the_assignment():
    with pytest.raises(request_mod.RequestError):
        request_mod.parse_request(
            json.dumps(
                {
                    "schema": "dsl.request/1",
                    "op": "teams.open_window",
                    "actor": "prof",
                    "course_org": COURSE,
                    "semester_org": SEMESTER,
                    "args": {},
                    "preview": True,
                }
            )
        )


def test_a_press_with_no_open_window_had_nothing_to_do(monkeypatch):
    monkeypatch.setattr(team_formation.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(team_formation, "open_windows", lambda *a: [])
    out = team_formation.run(COURSE, SEMESTER, NOW)
    assert out == 0 and out.conclusion == "nothing_to_do"
    assert out.text == "No team-formation window is open right now; nothing to send."


def test_a_window_nobody_is_waiting_on_has_nothing_to_send():
    window = SimpleNamespace(shut=False, waiting=())
    out = team_formation.notify_windows(
        COURSE, SEMESTER, Schedule(), [window], NOW, dry_run=True
    )
    assert out == 0 and out.conclusion == "nothing_to_do"
