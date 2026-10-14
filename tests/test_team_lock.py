"""The team-formation lock file (`semester-config/.system/assignments.lock.yml`): what it
says for each assignment, and when it is written. The Join-team form reads it and nothing
else.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml

from dsl_course import (
    discovery,
    gh_contents,
    grading_spec,
    settings,
    team_formation,
)
from dsl_course.schedule import AssignmentEntry, Schedule
from tests.conftest import run_settings, split_grading

# ------------------------------------------------- the team-formation lock file


_DUE = datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc)
# The late cutoff the window closes at: the due date plus the institution's 10 days.
_CUTOFF = _DUE + timedelta(days=10)
_HANDOUT = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


def _sched(*, handout: datetime | None = None, **assignments) -> Schedule:
    """A schedule of assignments whose only interesting fields are which template they
    hand out from and, for the formation window, when. `handout=None` is the manual
    hand-out every test that is not about the window wants."""
    return Schedule(
        assignments={
            key: AssignmentEntry(
                due_datetime=_DUE,
                course_source_repo=template,
                handout_datetime=handout,
            )
            for key, template in assignments.items()
        }
    )


def _lock(
    monkeypatch,
    sched,
    configs: dict[str, str | None],
    defaults: str = "",
    now: datetime | None = None,
) -> str:
    """Render the lock file for `sched`, with each template's `grading_config.yml` given
    as text (None = the template has none) and the course's own defaults block as YAML."""
    # Each template's run settings are its schedule key's block in the semester's
    # assignments.yml now (decision 0009); the rest stays the template's.
    split = {t: split_grading(text) for t, text in configs.items()}
    blocks = {
        key: yaml.safe_load(split[e.course_source_repo][1])["defaults"]
        for key, e in sched.assignments.items()
        if (split.get(e.course_source_repo) or (None, None))[1]
    }
    sched.org = sched.org or "SEMESTER"
    monkeypatch.setattr(
        grading_spec,
        "_grading_text",
        lambda org, template: (split.get(template) or (None,))[0],
    )
    run_settings(
        monkeypatch,
        lambda org: yaml.safe_dump({"assignments": blocks}) if blocks else None,
    )
    monkeypatch.setattr(
        settings, "org_meta", lambda org: yaml.safe_load(defaults or "{}") or {}
    )
    return team_formation.team_lock_text(
        team_formation.team_lock_entries("COURSE", sched, now)
    )


def test_the_lock_file_carries_a_scalar_block_per_schedule_assignment(monkeypatch):
    # The Join-team form runs in a PUBLIC repo under a token deliberately scoped away from
    # the course org's templates, so it cannot read grading_config.yml. This file is the
    # mirror it reads instead, and it must answer for every assignment in the schedule.
    text = _lock(
        monkeypatch,
        _sched(a1="assignment-1-f2026", project="assignment-4-project-f2026"),
        {
            "assignment-1-f2026": "type: individual\n",
            "assignment-4-project-f2026": (
                "type: group\nteam_formation: self_select\nmax_team_size: 3\n"
            ),
        },
    )
    assert yaml.safe_load(text) == {
        "assignments": {
            "a1": {
                "team_formation": "none",
                "max_team_size": 5,
                "team_formation_window": "none",
                # No window, so no day for a refusal about one to name.
                "team_formation_closes": None,
                "team_formation_page": None,
                # The students.csv column the form writes: no number, so the key.
                "team_column": "team:a1",
            },
            "project": {
                "team_formation": "self_select",
                "max_team_size": 3,
                # No handout_datetime on `_sched`, so the window never opens.
                "team_formation_window": "closed",
                "team_formation_closes": _CUTOFF.date(),
                # No `pages` handed in, so none to link - the key is still written.
                "team_formation_page": None,
                "team_column": "team:project",
            },
        }
    }
    # SYSTEM-OWNED, stamped at the write site - it is rewritten on every sync.
    assert text.startswith("# SYSTEM-OWNED")


def test_an_assigned_group_assignment_is_locked_as_assigned(monkeypatch):
    text = _lock(
        monkeypatch,
        _sched(project="assignment-4-project-f2026"),
        {"assignment-4-project-f2026": "type: group\nteam_formation: assigned\n"},
    )
    assert (
        yaml.safe_load(text)["assignments"]["project"]["team_formation"] == "assigned"
    )


def test_a_template_that_does_not_exist_yet_is_locked_to_none(monkeypatch, capsys):
    # Maths a2-a4 today: the schedule names a template nobody has created. Guessing
    # `group` would let any student mint a real GitHub team, granted `maintain` on the
    # repo, under a name of their choosing - so the lock refuses and says what that costs.
    text = _lock(
        monkeypatch, _sched(a2="assignment-2-f2026"), {"assignment-2-f2026": None}
    )
    assert yaml.safe_load(text)["assignments"]["a2"]["team_formation"] == "none"
    err = capsys.readouterr().err
    assert "no grading_config.yml yet" in err and "no team can be formed" in err


def test_the_course_default_cap_fills_in_for_an_assignment_that_names_none(monkeypatch):
    text = _lock(
        monkeypatch,
        _sched(project="assignment-4-project-f2026"),
        {"assignment-4-project-f2026": "type: group\n"},
        defaults="defaults:\n  max_team_size: 4\n",
    )
    assert yaml.safe_load(text)["assignments"]["project"]["max_team_size"] == 4


def test_a_semester_with_no_assignments_still_gets_a_readable_lock_file(monkeypatch):
    # The form refuses every slug rather than 404ing on the read and reporting a fault.
    text = _lock(monkeypatch, _sched(), {})
    assert yaml.safe_load(text) == {"assignments": {}}


def test_a_self_select_window_is_pending_then_open_then_closed(
    monkeypatch,
):
    # The three answers off one schedule, so the boundaries are read from the same dates a
    # semester really carries: handout 20 Sep, due 4 Oct, late cutoff 14 Oct.
    def window(now: datetime) -> str:
        text = _lock(
            monkeypatch,
            _sched(project="assignment-4-project-f2026", handout=_HANDOUT),
            {
                "assignment-4-project-f2026": "type: group\nteam_formation: self_select\n"
            },
            now=now,
        )
        return yaml.safe_load(text)["assignments"]["project"]["team_formation_window"]

    # `pending` and `closed` are kept apart because the form says different things about
    # them: before the handout the close date is still in the FUTURE, so the shut wording
    # would tell a September student the door closed in October.
    assert window(_HANDOUT - timedelta(seconds=1)) == "pending"
    assert window(_HANDOUT) == "open"  # the handout instant itself is inside
    assert window(_CUTOFF - timedelta(seconds=1)) == "open"
    assert window(_CUTOFF) == "closed"  # the cutoff is not: the snapshot has frozen


def test_a_group_assignment_nobody_declared_a_formation_for_forms_no_team(monkeypatch):
    # Decision 0039: with no `team_formation` at any layer nobody forms teams, so the lock
    # says `none` and the Join-team form refuses it. `assigned` only when declared.
    sched = _sched(p="group-f2026", q="assigned-f2026", handout=_HANDOUT)
    text = _lock(
        monkeypatch,
        sched,
        {
            "group-f2026": "type: group\n",
            "assigned-f2026": "type: group\nteam_formation: assigned\n",
        },
        now=_HANDOUT,
    )
    lock = yaml.safe_load(text)["assignments"]
    assert (lock["p"]["team_formation"], lock["p"]["team_formation_window"]) == (
        "none",
        "none",
    )
    assert (lock["q"]["team_formation"], lock["q"]["team_formation_window"]) == (
        "assigned",
        "none",
    )
    assert team_formation.self_select_keys("COURSE", sched) == []
    spec = grading_spec.load_grading_spec(
        "COURSE", "group-f2026", semester_org=sched.org, slug="p"
    )
    assert dict(spec.sources)["team_formation"] == "institution"


def test_a_self_select_assignment_nobody_has_dated_never_opens(monkeypatch):
    # `handout_datetime` unset = handed out by hand at a moment nobody wrote down, so
    # there is no hour from which "form your team now" would be true.
    text = _lock(
        monkeypatch,
        _sched(project="assignment-4-project-f2026"),
        {"assignment-4-project-f2026": "type: group\nteam_formation: self_select\n"},
        now=_HANDOUT,
    )
    assert (
        yaml.safe_load(text)["assignments"]["project"]["team_formation_window"]
        == "closed"
    )


def test_an_assignment_the_form_refuses_anyway_has_no_window(monkeypatch):
    # `assigned` and `individual` are refused on the team_formation scalar alone, so the
    # window says nothing - whatever the dates would make of it.
    text = _lock(
        monkeypatch,
        _sched(
            a1="assignment-1-f2026",
            project="assignment-4-project-f2026",
            handout=_HANDOUT,
        ),
        {
            "assignment-1-f2026": "type: individual\n",
            "assignment-4-project-f2026": "type: group\nteam_formation: assigned\n",
        },
        now=_HANDOUT,
    )
    windows = yaml.safe_load(text)["assignments"]
    assert windows["a1"]["team_formation_window"] == "none"
    assert windows["project"]["team_formation_window"] == "none"


def test_the_close_date_is_the_pins_day_and_the_line_is_there_even_when_it_is_empty(
    monkeypatch,
):
    # The only reader is a refusal a student reads ("closed on 14 Oct"), so the scalar is
    # the cutoff's DAY, not its moment. The line is written whatever the value: the form and
    # `welcome.open_formation_slugs` both line-scan this file, and a key that appears only
    # sometimes is a second shape for them to get right.
    text = _lock(
        monkeypatch,
        _sched(
            a1="assignment-1-f2026",
            project="assignment-4-project-f2026",
            handout=_HANDOUT,
        ),
        {
            "assignment-1-f2026": "type: individual\n",
            "assignment-4-project-f2026": "type: group\nteam_formation: self_select\n",
        },
        now=_HANDOUT,
    )
    assert "    team_formation_closes: 2026-10-14\n" in text
    assert "    team_formation_closes:\n" in text  # the individual one, empty
    entries = yaml.safe_load(text)["assignments"]
    assert entries["project"]["team_formation_closes"] == _CUTOFF.date()
    assert entries["a1"]["team_formation_closes"] is None


def test_a_template_with_no_spec_has_no_window_either(monkeypatch):
    text = _lock(
        monkeypatch,
        _sched(a2="assignment-2-f2026", handout=_HANDOUT),
        {"assignment-2-f2026": None},
        now=_HANDOUT,
    )
    assert yaml.safe_load(text)["assignments"]["a2"]["team_formation_window"] == "none"


def _writes(
    monkeypatch, existing, ok: bool = True, console: bool = False
) -> list[dict]:
    """Stub everything the lock write touches and collect what it puts. `existing` is what
    `get_file_with_sha` answers: `(text, sha)`, None for a 404, or an exception to raise.
    `console` is the course's `student_console:`."""
    monkeypatch.setattr(
        grading_spec, "_grading_text", lambda org, t, **_: "type: group\n"
    )
    # The course self-selects for every group assignment.
    monkeypatch.setattr(
        settings,
        "org_meta",
        lambda org: {"defaults": {"team_formation": "self_select"}},
    )
    monkeypatch.setattr(
        discovery, "org_meta", lambda org: {"course": {"student_console": console}}
    )
    monkeypatch.setattr(
        team_formation, "repo_is_archived", lambda org, repo, **_: False
    )

    def read(org, repo, path):
        if isinstance(existing, Exception):
            raise existing
        return existing

    monkeypatch.setattr(team_formation, "get_file_with_sha", read)
    puts: list[dict] = []

    def put(org, repo, path, content, msg, expected_sha=None):
        puts.append({"org": org, "path": path, "content": content, "sha": expected_sha})
        return ok

    monkeypatch.setattr(team_formation, "put_file", put)
    return puts


def test_the_lock_file_is_written_once_and_is_free_when_nothing_changed(monkeypatch):
    # put_file blob-compares, so writing it from the membership sync, the handout and the
    # nightly refresh costs a read apiece and no commit at all on an unchanged semester.
    puts = _writes(monkeypatch, None)
    assert team_formation.sync_team_lock(
        "COURSE", "SEMESTER", _sched(project="assignment-4-project-f2026")
    ).ok
    (put,) = puts
    assert (put["org"], put["path"]) == ("SEMESTER", ".system/assignments.lock.yml")
    assert yaml.safe_load(put["content"])["assignments"]["project"] == {
        "team_formation": "self_select",
        "max_team_size": 5,
        "team_formation_window": "closed",
        "team_formation_closes": _CUTOFF.date(),
        # Where the teams are listed: the assignment's page on the semester site.
        "team_formation_page": "https://semester.github.io/assignments/project.html",
        "team_column": "team:project",
    }


def test_a_course_that_opts_in_locks_the_page_in_the_student_console(monkeypatch):
    puts = _writes(monkeypatch, None, console=True)
    team_formation.sync_team_lock(
        "COURSE", "SEMESTER", _sched(project="assignment-4-project-f2026")
    )
    entry = yaml.safe_load(puts[0]["content"])["assignments"]["project"]
    assert entry["team_formation_page"] == (
        "https://hertie-data-science-lab.github.io/"
        "dsl-teaching-toolkit/?semester=SEMESTER#assignment-project"
    )


def test_the_lock_links_no_page_for_an_individual_assignment_and_lists_nothing(
    monkeypatch,
):
    # The page is for the refusal of a Join, which only a self-select assignment gets.
    puts = _writes(monkeypatch, None)
    monkeypatch.setattr(
        grading_spec, "_grading_text", lambda org, t: "type: individual\n"
    )
    assert team_formation.sync_team_lock(
        "COURSE", "SEMESTER", _sched(a1="assignment-1")
    ).ok
    assert "    team_formation_page:\n" in puts[0]["content"].decode()


def test_the_lock_write_says_whether_the_file_actually_moved(monkeypatch):
    # `changed` is the blob compare handed back, so a caller that renders off this file
    # knows when to re-render without paying a second read to find out.
    puts = _writes(monkeypatch, None)
    sched = _sched(project="assignment-4-project-f2026")
    assert team_formation.sync_team_lock("COURSE", "SEMESTER", sched) == (True, True)
    live = puts[0]["content"]

    puts = _writes(monkeypatch, ("text does not matter", gh_contents.blob_sha(live)))
    assert team_formation.sync_team_lock("COURSE", "SEMESTER", sched) == (True, False)
    # The sha the content was read at goes to put_file - both the safe read-modify-write
    # and, when it already matches, the no-op short circuit.
    assert puts[0]["sha"] == gh_contents.blob_sha(live)

    puts = _writes(monkeypatch, ("something else", "0" * 40))
    assert team_formation.sync_team_lock("COURSE", "SEMESTER", sched) == (True, True)


def test_a_read_that_failed_still_writes_and_reports_a_change(monkeypatch):
    # Not a 404 - the file may well be there and unreadable. `changed` is a render HINT,
    # so one spurious re-render beats a missed one, and the write goes ahead with no sha.
    puts = _writes(monkeypatch, RuntimeError("could not read"))
    assert team_formation.sync_team_lock(
        "COURSE", "SEMESTER", _sched(project="assignment-4-project-f2026")
    ) == (True, True)
    assert puts[0]["sha"] is None


def test_a_failed_write_is_never_reported_as_a_change(monkeypatch):
    puts = _writes(monkeypatch, None, ok=False)
    assert team_formation.sync_team_lock("COURSE", "SEMESTER", _sched(p="t")) == (
        False,
        False,
    )
    assert len(puts) == 1


def test_a_lock_file_that_could_not_be_written_says_what_that_costs(
    monkeypatch, capsys
):
    _writes(monkeypatch, None, ok=False)
    assert not team_formation.sync_team_lock("COURSE", "SEMESTER", _sched(p="t")).ok
    assert "the Join-team form reads it" in capsys.readouterr().err


def test_a_closed_out_semester_is_left_alone(monkeypatch, capsys):
    # `teardown` archives semester-config last, and an archived repo is read-only. The
    # membership sync fans out over the semester REGISTRY, which teardown does not touch, so
    # without this every finished semester would 403 the daily sync red for good.
    def boom(*args, **kwargs):
        raise AssertionError("a sealed semester-config takes no write")

    monkeypatch.setattr(team_formation, "repo_is_archived", lambda org, repo: True)
    monkeypatch.setattr(team_formation, "put_file", boom)
    monkeypatch.setattr(grading_spec, "_grading_text", boom)
    assert team_formation.sync_team_lock("COURSE", "SEMESTER", _sched(p="t")).ok
    assert "semester archived" in capsys.readouterr().out


def test_no_log_line_from_the_lock_file_names_a_person(monkeypatch, capsys):
    # It runs in the course org's PUBLIC .github. Only slugs and template names may appear.
    monkeypatch.setattr(team_formation, "put_file", lambda *a, **k: True)
    _lock(monkeypatch, _sched(a2="assignment-2-f2026"), {"assignment-2-f2026": None})
    printed = capsys.readouterr()
    assert "@" not in printed.out + printed.err


def test_the_lock_keeps_the_fragment_of_the_assignment_page_url():
    # The page is the assignment's page in the console (`...?semester=x#assignment-a`): a
    # `#` inside a value is not a comment, as in YAML, so the fragment survives the parse.
    page = "https://console.example/?semester=x#assignment-a"
    text = team_formation.team_lock_text(
        {"a": ("self_select", 4, "open", "2026-10-04", page)}
    )
    assert (
        team_formation.parse_team_lock(text + "# a comment\n")["a"][
            "team_formation_page"
        ]
        == page
    )
