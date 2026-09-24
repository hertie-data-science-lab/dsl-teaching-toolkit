"""relink: a student re-pointed at a new GitHub account in students.csv is moved there by
the next membership sync - repos, grants, config rows, throttle issues - with the stored
`github_id` written last, so a run that stops anywhere is finished by the next.

Everything `relink` reaches GitHub through is stubbed by the name it imported, over one
in-memory `World`; the enrol-code writer runs for real against a stubbed Contents API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dsl_course import enrol_codes, grades, relink, roster, teams
from dsl_course.gh_contents import blob_sha, read_csv
from tests.conftest import ROSTER_HEADER, repo_row

ORG = "Cohort"
OLD, NEW = "oldacct", "newacct"
OLD_ID, NEW_ID = "101", "202"
SHEET = "grading_sheets/assignment-1.yml"
GROUP_SHEET = "grading_sheets/assignment-2.yml"
SPEC = grades.SheetSpec(
    slug="assignment-1", title="A1", is_group=False, questions={"q1": "5"}
)
GROUP = grades.SheetSpec(slug="assignment-2", title="A2", is_group=True)


def roster_text(handle=NEW, stored=OLD_ID, extra=()) -> str:
    rows = [f"a@x.edu,Ada,,{handle},{stored},dsl-aaaaaa,", "b@x.edu,Bob,,bob,303,,"]
    return "\n".join([ROSTER_HEADER, *rows, *extra]) + "\n"


def sheet_text(new_entry: str | None = None, status: str = "FROZEN at the cutoff"):
    units = [(OLD, [OLD]), ("bob", ["bob"])]
    if new_entry is not None:
        units.append((NEW, [NEW]))
    sheet = grades.new_sheet(SPEC, units)
    sheet["submissions"][OLD]["score_individual"]["q1"] = "4"
    sheet["submissions"][OLD]["feedback_individual"] = "Good work.\nKeep going."
    if new_entry:
        sheet["submissions"][NEW]["feedback_individual"] = new_entry
    return grades.dump_sheet(sheet, SPEC, status)


def group_sheet_text() -> str:
    sheet = grades.new_sheet(GROUP, [("team-x", [OLD, "bob"])])
    sheet["teams"]["team-x"]["members"][OLD]["feedback_individual"] = "led the analysis"
    return grades.dump_sheet(sheet, GROUP, "open")


def distributed_text() -> str:
    return grades.dump_distributed(
        {
            (OLD, "", "email"): ("abc", "2026-09-01", ""),
            (OLD, "assignment-1", "gradebook"): ("def", "2026-09-01", ""),
            ("bob", "", "email"): ("ghi", "2026-09-01", ""),
        }
    )


class World:
    """One cohort as `relink` sees it: its roster, classroom-config, repos and people."""

    def __init__(self):
        self.roster = roster_text()
        self.config: dict[str, bytes] = {
            teams.TEAMS_PATH: (
                ",".join(teams.FIELDS) + "\nassignment-2,team-x,oldacct\n"
                "assignment-2,team-x,bob\n"
            ).encode(),
            SHEET: sheet_text().encode(),
            GROUP_SHEET: group_sheet_text().encode(),
            grades.DISTRIBUTED_PATH: distributed_text().encode(),
            f"autograde/assignment-1/{OLD}.json": b'{"score": 4}\n',
            "autograde/assignment-1/bob.json": b'{"score": 5}\n',
        }
        bot = [("dsl-bot", "")]
        self.repos: dict[str, dict] = {
            "assignment-1": {"template": True, "commits": bot, "collab": {}},
            f"assignment-1-{OLD}": {
                "commits": bot + [(OLD, "")],
                "collab": {OLD: "maintain"},
            },
            f"grades-{OLD}": {"commits": bot, "collab": {OLD: "pull"}},
            "assignment-1-bob": {"commits": bot, "collab": {"bob": "maintain"}},
            "assignment-2-team-x": {"commits": bot, "collab": {}},
        }
        self.users = {OLD: OLD_ID, NEW: NEW_ID, "bob": "303"}
        self.subjects: tuple[str, ...] | None = (
            "roster: link @bob (id 303)",
            f"roster: link @{OLD} (id {OLD_ID})",
            "Edit students.csv",
        )
        self.issues = {NEW: [11, 12, 13]}
        self.closed: list[tuple[str, int]] = []
        self.archived: set[str] = set()
        self.fail: str | None = None
        self.fail_times = 1
        self.specs = {"assignment-1": SPEC}
        self.gone: dict[str, str | None] = {}
        self.head = 0
        self.race = None
        self.org: dict[str, str | None] = {OLD: "member", "bob": "member"}
        self.faculty: dict[str, set[str] | None] = {"instructors": {"prof"}}

    def listing(self) -> dict[str, dict]:
        return {
            name: repo_row(
                name, isTemplate=bool(r.get("template")), archived=name in self.archived
            )
            for name, r in self.repos.items()
        }

    def failing(self, step: str) -> bool:
        if self.fail != step:
            return False
        self.fail_times -= 1
        if not self.fail_times:
            self.fail = None
        return True

    def stored_id(self) -> str:
        rows = list(read_csv(self.roster, roster.REQUIRED_FIELDS, "students.csv"))
        return rows[0]["github_id"]


@pytest.fixture
def world(monkeypatch) -> World:
    w = World()
    # "" is GitHub's definite 404; None (an entry set to None) is no answer at all.
    monkeypatch.setattr(
        relink, "id_of_login", lambda login: w.users.get(login.lower(), "")
    )
    monkeypatch.setattr(
        relink,
        "login_of_id",
        lambda uid: w.gone.get(
            uid, next((k for k, v in w.users.items() if v == uid), "")
        ),
    )

    def subjects(org, repo, path):
        if w.subjects is None:
            raise RuntimeError("history unreadable")
        return w.subjects

    monkeypatch.setattr(relink, "path_commit_subjects", subjects)

    def roster_read(org, repo, path, ref=""):
        return (w.roster, blob_sha(w.roster.encode()))

    monkeypatch.setattr(relink, "get_file_with_sha", roster_read)
    monkeypatch.setattr(enrol_codes, "get_file_with_sha", roster_read)

    def roster_write(org, repo, path, content, message, expected_sha=None, **k):
        if w.failing("id") or expected_sha != blob_sha(w.roster.encode()):
            return False
        w.roster = content.decode()
        return True

    monkeypatch.setattr(enrol_codes, "put_file", roster_write)

    def record(org, repo, path, content, message, person=False, **k):
        if w.failing("record"):
            return False
        w.config[path] = content
        return True

    monkeypatch.setattr(relink, "put_file", record)

    def commit(org, repo, files, message, *, delete=(), person=False, base=None):
        if w.race:
            w.race()  # somebody else commits between the relink's read and its write
            w.race = None
        if w.failing("config") or base != (f"c{w.head}", "tree"):
            return False  # a non-forced ref update off a stale parent: 422
        w.config.update(files)
        for path in delete:
            w.config.pop(path, None)
        w.head += 1
        return True

    monkeypatch.setattr(relink, "put_files", commit)
    monkeypatch.setattr(relink, "head_commit", lambda org, repo: (f"c{w.head}", "tree"))
    monkeypatch.setattr(
        relink, "org_member_role", lambda org, login: w.org.get(login.lower(), "")
    )
    monkeypatch.setattr(
        relink, "get_team_members", lambda org, team: w.faculty.get(team, set())
    )

    def remove(org, login):
        w.org.pop(login.lower(), None)
        return True

    monkeypatch.setattr(relink, "remove_org_membership", remove)
    monkeypatch.setattr(
        relink,
        "repo_blob_shas",
        lambda org, repo, branch: {p: blob_sha(c) for p, c in w.config.items()},
    )
    monkeypatch.setattr(
        relink,
        "get_blob",
        lambda org, repo, sha: next(c for c in w.config.values() if blob_sha(c) == sha),
    )
    monkeypatch.setattr(
        relink.teams,
        "load",
        lambda org: teams.parse(w.config[teams.TEAMS_PATH].decode()),
    )

    def rename(org, name, new_name, *, description=None, person=False):
        if w.failing("rename"):
            return False
        w.repos[new_name] = w.repos.pop(name)
        return True

    monkeypatch.setattr(relink, "rename_repo", rename)
    monkeypatch.setattr(
        relink,
        "archive_repo",
        lambda org, name, person=False: w.archived.add(name) or True,
    )
    monkeypatch.setattr(relink, "repo_missing", lambda org, name: name not in w.repos)
    monkeypatch.setattr(
        relink,
        "direct_collaborators",
        lambda org, repo, person=False: frozenset(
            k.casefold() for k in w.repos[repo]["collab"]
        ),
    )

    def grant(org, repo, login, permission="push", person=False):
        if w.failing("grant"):
            return False
        w.repos[repo]["collab"][login] = permission
        return True

    monkeypatch.setattr(relink, "add_collaborator", grant)

    def revoke(org, repo, login, dry_run=False):
        return (1 if w.repos[repo]["collab"].pop(login, None) else 0), 0

    monkeypatch.setattr(relink, "revoke_repo_grants", revoke)

    def close(repo, creator, label, comment):
        if w.failing("issues"):
            return 0, 1
        assert label == "needs-review" and OLD not in comment
        numbers = w.issues.pop(creator, [])
        w.closed += [(repo, n) for n in numbers]
        return len(numbers), 0

    monkeypatch.setattr(relink, "close_by_creator", close)
    monkeypatch.setattr(relink, "bot_login", lambda: "dsl-bot")
    monkeypatch.setattr(relink, "course_org_for_cohort", lambda org: "Course")
    monkeypatch.setattr(relink.schedule, "load", lambda org: None)
    monkeypatch.setattr(relink, "sheet_specs", lambda course, sched: w.specs)

    def gh(*args, **kwargs):
        repo = args[1].split("/")[2]
        rows = w.repos[repo]["commits"]
        return (0, "\n".join(f"{login}\t{email}" for login, email in rows))

    monkeypatch.setattr(relink, "gh", gh)
    return w


def run(w: World, dry_run: bool = False) -> tuple[int, dict]:
    existing = w.listing()
    return relink.sync(ORG, existing, dry_run=dry_run), existing


def sheet_of(w: World, path: str = SHEET) -> dict:
    return grades.parse_sheet(w.config[path].decode())


# ----------------------------------------------------------------- the whole move


def test_a_switched_account_is_moved_completely(world):
    errors, existing = run(world)
    assert errors == 0
    # Repos renamed, and the caller's listing with them.
    assert f"assignment-1-{NEW}" in world.repos and f"grades-{NEW}" in world.repos
    assert f"assignment-1-{OLD}" not in world.repos
    assert f"assignment-1-{NEW}" in existing and f"grades-{OLD}" not in existing
    # Grants: the new login holds what the old one held; the old one holds nothing.
    assert world.repos[f"assignment-1-{NEW}"]["collab"] == {NEW: "maintain"}
    assert world.repos[f"grades-{NEW}"]["collab"] == {NEW: "pull"}
    assert world.repos["assignment-1-bob"]["collab"] == {"bob": "maintain"}
    # classroom-config
    assert "assignment-2,team-x,newacct" in world.config[teams.TEAMS_PATH].decode()
    assert OLD not in world.config[teams.TEAMS_PATH].decode()
    marks = sheet_of(world)["submissions"]
    assert OLD not in marks and marks[NEW]["score_individual"] == {"q1": "4"}
    assert list(marks) == [NEW, "bob"]
    assert list(sheet_of(world, GROUP_SHEET)["teams"]["team-x"]["members"]) == [
        NEW,
        "bob",
    ]
    distributed = grades.parse_distributed(
        world.config[grades.DISTRIBUTED_PATH].decode()
    )
    assert (NEW, "", "email") in distributed and (OLD, "", "email") not in distributed
    assert (OLD, "assignment-1", "gradebook") in distributed  # redistributed later
    assert f"autograde/assignment-1/{NEW}.json" in world.config
    assert f"autograde/assignment-1/{OLD}.json" not in world.config
    # The throttle issues, the private record, and the id - last.
    assert world.closed == [
        (f"{ORG}/welcome", 11),
        (f"{ORG}/welcome", 12),
        (f"{ORG}/welcome", 13),
    ]
    record = json.loads(world.config[f"enrolment/relinks/{OLD_ID}.json"])
    assert (record["old_login"], record["new_login"]) == (OLD, NEW)
    assert (record["old_id"], record["new_id"]) == (OLD_ID, NEW_ID)
    assert world.stored_id() == NEW_ID
    # Nothing else in the roster moved.
    assert world.roster.splitlines()[2] == "b@x.edu,Bob,,bob,303,,"


def test_the_sheet_edit_keeps_the_header_and_the_graders_text(world):
    before = world.config[SHEET].decode()
    run(world)
    after = world.config[SHEET].decode()
    assert grades.sheet_is_frozen(after)
    assert grades.sheet_header(after) == grades.sheet_header(before)
    assert after == before.replace(f"  {OLD}:\n", f"  {NEW}:\n")


def test_a_second_run_does_nothing(world):
    run(world)
    snapshot = (dict(world.config), world.roster, dict(world.repos))
    assert run(world)[0] == 0
    assert (dict(world.config), world.roster, dict(world.repos)) == snapshot


def test_the_roster_memo_is_cleared_once_the_id_moves(world, monkeypatch):
    monkeypatch.setattr(roster, "get_file_content", lambda *a, **k: world.roster)
    roster.load(ORG)
    assert roster._roster_text.cache_info().currsize == 1
    run(world)
    assert roster._roster_text.cache_info().currsize == 0
    assert roster.load(ORG)[0].github_id == NEW_ID


def test_a_dry_run_moves_nothing(world):
    before = (dict(world.config), world.roster, set(world.repos))
    assert run(world, dry_run=True)[0] == 0
    assert (dict(world.config), world.roster, set(world.repos)) == before


@pytest.mark.parametrize(
    "step", ["rename", "grant", "config", "issues", "record", "id"]
)
def test_a_failure_at_any_step_leaves_the_id_and_the_next_run_finishes(world, step):
    world.fail = step
    # The roster write retries on its own; a failure there is one that outlasts them.
    world.fail_times = enrol_codes.WRITE_ATTEMPTS if step == "id" else 1
    assert run(world)[0] == 1
    assert world.stored_id() == OLD_ID, "the id must be the last thing written"
    assert run(world)[0] == 0
    assert world.stored_id() == NEW_ID
    assert set(world.repos) >= {f"assignment-1-{NEW}", f"grades-{NEW}"}
    assert world.repos[f"assignment-1-{NEW}"]["collab"] == {NEW: "maintain"}
    assert NEW in sheet_of(world)["submissions"]
    assert f"autograde/assignment-1/{NEW}.json" in world.config
    assert len(world.closed) == 3


def test_nothing_that_names_a_student_reaches_the_public_log(world, capsys):
    run(world)
    out = capsys.readouterr()
    assert OLD not in out.out + out.err and NEW not in out.out + out.err
    assert "relinked 1 student(s)" in out.out


# ---------------------------------------------------------------------- detection


def pending(world) -> list[relink.Pending]:
    return relink.pending(ORG, roster.parse(world.roster))


def test_the_row_is_found_with_both_accounts(world):
    (p,) = pending(world)
    assert (p.old, p.old_id, p.new, p.new_id, p.line) == (OLD, OLD_ID, NEW, NEW_ID, 2)


def test_the_same_id_is_a_rename_and_not_a_relink(world):
    world.roster = roster_text(handle=OLD)
    world.subjects = None  # never asked for
    assert pending(world) == []


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda w: w.users.pop(NEW), id="handle-404"),
        pytest.param(lambda w: w.users.update({NEW: None}), id="handle-unanswered"),
        pytest.param(lambda w: w.gone.update({OLD_ID: ""}), id="stored-id-404"),
        pytest.param(lambda w: w.gone.update({OLD_ID: None}), id="stored-unanswered"),
        pytest.param(
            lambda w: setattr(w, "roster", roster_text(stored="")), id="no-stored-id"
        ),
        pytest.param(
            lambda w: setattr(
                w, "roster", roster_text(extra=[f"c@x.edu,Cy,,{NEW.upper()},,,"])
            ),
            id="duplicate-handle",
        ),
        pytest.param(
            lambda w: setattr(
                w, "roster", roster_text(extra=[f"c@x.edu,Cy,,cy,{NEW_ID},,"])
            ),
            id="new-id-on-another-row",
        ),
        pytest.param(
            lambda w: setattr(w, "subjects", (f"roster: link @{NEW} (id {OLD_ID})",)),
            id="squat-guard",
        ),
        pytest.param(lambda w: setattr(w, "subjects", None), id="history-unreadable"),
        pytest.param(
            lambda w: setattr(
                w, "roster", roster_text(extra=[f"c@x.edu,Cy,,{OLD},,,"])
            ),
            id="old-login-still-on-the-roster",
        ),
    ],
)
def test_a_row_that_cannot_be_told_for_certain_is_left_alone(world, setup):
    setup(world)
    before = (world.roster, dict(world.config), set(world.repos))
    assert pending(world) == []
    assert run(world)[0] == 0
    assert (world.roster, dict(world.config), set(world.repos)) == before


def test_more_than_the_cap_in_one_pass_acts_on_none(world):
    extra = []
    for n in range(relink.MAX_PER_PASS):
        world.users[f"was{n}"] = f"9{n}"
        world.users[f"now{n}"] = f"8{n}"
        extra.append(f"s{n}@x.edu,S,,now{n},9{n},,")
    world.roster = roster_text(extra=extra)
    assert pending(world) == []
    world.roster = roster_text(extra=extra[:-1])
    assert len(pending(world)) == relink.MAX_PER_PASS


# --------------------------------------------------------------------- collisions


def test_an_untouched_new_name_repo_is_set_aside_and_archived(world):
    world.repos[f"grades-{NEW}"] = {
        "commits": [("dsl-bot", "")],
        "collab": {NEW: "pull"},
    }
    assert run(world)[0] == 0
    assert "relink-aside-1" in world.archived
    assert world.repos[f"grades-{NEW}"]["collab"] == {NEW: "pull"}
    assert world.stored_id() == NEW_ID


def test_a_new_name_repo_with_work_in_it_refuses_and_moves_nothing(world, capsys):
    world.repos[f"assignment-1-{NEW}"] = {"commits": [(NEW, "")], "collab": {}}
    before = (dict(world.config), world.roster, set(world.repos))
    assert run(world)[0] == 0, "a refusal is not a red run"
    assert (dict(world.config), world.roster, set(world.repos)) == before
    err = capsys.readouterr().err
    assert f"relink of @{NEW}" in err and OLD not in err


def test_an_identical_team_row_is_dropped(world):
    world.config[teams.TEAMS_PATH] += b"assignment-2,team-x,newacct\n"
    run(world)
    text = world.config[teams.TEAMS_PATH].decode()
    assert text.count("newacct") == 1 and OLD not in text


def test_a_different_team_for_one_assignment_refuses(world):
    world.config[teams.TEAMS_PATH] += b"assignment-2,team-y,newacct\n"
    run(world)
    assert world.stored_id() == OLD_ID and f"assignment-1-{OLD}" in world.repos


def test_a_blank_new_sheet_entry_is_dropped_and_the_old_one_re_keyed(world):
    world.config[SHEET] = sheet_text(new_entry="").encode()
    run(world)
    marks = sheet_of(world)["submissions"]
    assert list(marks) == [NEW, "bob"]
    assert marks[NEW]["feedback_individual"] == "Good work.\nKeep going."


def test_marks_on_both_sheet_entries_refuse(world):
    world.config[SHEET] = sheet_text(new_entry="also marked").encode()
    run(world)
    assert world.stored_id() == OLD_ID
    assert OLD in sheet_of(world)["submissions"]


def test_autograde_results_for_both_accounts_refuse(world):
    world.config[f"autograde/assignment-1/{NEW}.json"] = b"{}"
    run(world)
    assert world.stored_id() == OLD_ID
    assert f"autograde/assignment-1/{OLD}.json" in world.config


def test_an_unreadable_sheet_holds_the_relink(world):
    world.config[SHEET] = b"submissions: [unclosed\n"
    run(world)
    assert world.stored_id() == OLD_ID


def test_the_squat_guard_matches_the_message_onboard_commits():
    # The guard recognises a pair the toolkit linked by onboard's own commit message, so
    # the two spellings are one contract across two languages.
    onboard = (Path(__file__).parents[1] / "templates/welcome/onboard.yml").read_text()
    js = relink.LINK_MESSAGE.format(handle="${handle}", user_id="${userId}")
    assert f"message: `{js}`" in onboard


# ------------------------------------------------------------ racing writes


def test_a_config_edit_that_lands_mid_relink_is_never_overwritten(world, capsys):
    # The classroom-config commit is built on the commit its files were READ at, so a
    # write that landed in between makes it fail rather than be silently reverted.
    def faculty_edit():
        world.config[teams.TEAMS_PATH] += b"assignment-2,team-x,cy\n"
        world.head += 1

    world.race = faculty_edit
    assert run(world)[0] == 0, "a lost race is transient, not a red run"
    assert world.stored_id() == OLD_ID
    assert OLD in world.config[teams.TEAMS_PATH].decode()
    assert "met a concurrent classroom-config edit" in capsys.readouterr().out
    assert run(world)[0] == 0
    text = world.config[teams.TEAMS_PATH].decode()
    assert "cy" in text and "newacct" in text and OLD not in text
    assert world.stored_id() == NEW_ID


def test_the_id_write_never_reverts_a_roster_edit_that_landed_meanwhile(
    world, monkeypatch
):
    # `write_column` sends the sha it read at and re-applies only this row's cell on a
    # refusal, so a row somebody added in between survives.
    real = enrol_codes.put_file
    added = "c@x.edu,Cy,,,,,"

    def racing(*a, **k):
        if added not in world.roster:
            world.roster += added + "\n"
        return real(*a, **k)

    monkeypatch.setattr(enrol_codes, "put_file", racing)
    assert run(world)[0] == 0
    assert world.stored_id() == NEW_ID and added in world.roster


# ------------------------------------------------------------ the old account


def test_the_old_account_leaves_the_org(world):
    run(world)
    assert OLD not in world.org and "bob" in world.org


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda w: w.org.update({OLD: "admin"}), id="owner"),
        pytest.param(lambda w: w.faculty.update({"instructors": {OLD}}), id="faculty"),
    ],
)
def test_the_old_account_is_kept_when_it_may_be_staff(world, setup):
    setup(world)
    assert run(world)[0] == 0
    assert OLD in world.org and world.stored_id() == NEW_ID


def test_an_old_account_already_gone_is_done(world):
    world.org.pop(OLD)
    assert run(world)[0] == 0
    assert world.stored_id() == NEW_ID


# ------------------------------------------------------------ lookup noise


def test_an_outage_is_one_public_line_not_one_per_student(world, capsys):
    for n in range(5):
        world.users[f"s{n}"] = None
    extra = [f"s{n}@x.edu,S,,s{n},9{n},," for n in range(5)]
    world.roster = roster_text(stored=NEW_ID, extra=extra)
    run(world)
    err = capsys.readouterr().err
    assert err.count("could not be checked") == 1 and "5 roster row(s)" in err


def test_a_deleted_old_account_is_one_line_for_faculty(world, capsys):
    world.gone[OLD_ID] = ""
    run(world)
    err = capsys.readouterr().err
    assert err.count("has been deleted") == 1 and "could not be checked" not in err
    assert NEW not in err  # the handle itself is verbose-only


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda w: w.faculty.update({"course-admin": None}), id="teams"),
        pytest.param(lambda w: w.org.update({OLD: None}), id="role"),
    ],
)
def test_an_unclear_membership_answer_holds_the_id_for_the_next_sync(world, setup):
    setup(world)
    assert run(world)[0] == 1
    assert OLD in world.org and world.stored_id() == OLD_ID
    world.org[OLD], world.faculty = "member", {}
    assert run(world)[0] == 0
    assert OLD not in world.org and world.stored_id() == NEW_ID


def test_a_mistyped_handle_is_named_for_faculty_not_counted_as_unanswered(
    world, capsys
):
    world.users.pop(NEW)
    run(world)
    err = capsys.readouterr().err
    assert err.count(f"no GitHub account is called @{NEW}") == 1
    assert "could not be checked" not in err


# ------------------------------------------------------------ the grant


def test_a_student_choice_repo_is_granted_admin_as_provisioning_does(world):
    world.specs = {
        "assignment-1": grades.SheetSpec(
            slug="assignment-1", title="A1", is_group=False, visibility="student_choice"
        )
    }
    run(world)
    assert world.repos[f"assignment-1-{NEW}"]["collab"] == {NEW: "admin"}
    assert world.repos[f"grades-{NEW}"]["collab"] == {NEW: "pull"}


def test_the_grant_follows_provisioning_even_when_the_old_one_was_pruned(world):
    # An unfinished relink's pass ends in sync_roster's prune, which revokes the old
    # login's grant - so the old grant is no guide to what the new login should get.
    world.repos[f"assignment-1-{OLD}"]["collab"] = {}
    run(world)
    assert world.repos[f"assignment-1-{NEW}"]["collab"] == {NEW: "maintain"}
