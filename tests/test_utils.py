"""Session directory helpers: sections/sessions are discovered from the directory
structure itself (any dir with an ordinal-prefixed subdir is a section) - no declared
config, so these pure functions are the whole contract."""

from __future__ import annotations

import json

import pytest

from dsl_course import utils


def test_session_number_extracts_ordinal_prefix():
    assert utils.session_number("00_intro") == 0
    assert utils.session_number("07_finals-review") == 7
    assert utils.session_number("13_other") == 13
    assert utils.session_number("3_regression") == 3
    assert utils.session_number("no-prefix-here") is None


def test_find_session_dir_plain_and_padded(tmp_path):
    section = tmp_path / "lectures"
    section.mkdir()
    (section / "00_intro").mkdir()
    (section / "03_regression").mkdir()  # zero-padded
    (section / "13_other").mkdir()  # must not match session "3"
    assert utils.find_session_dir(section, "3").name == "03_regression"
    assert utils.find_session_dir(section, "13").name == "13_other"
    assert utils.find_session_dir(section, "9") is None


def test_find_session_dir_missing_section_returns_none(tmp_path):
    assert utils.find_session_dir(tmp_path / "does-not-exist", "1") is None


def test_discover_sections_only_counts_dirs_with_ordinal_subdirs(tmp_path):
    (tmp_path / "lectures" / "00_intro").mkdir(parents=True)
    (tmp_path / "labs" / "03_regression").mkdir(parents=True)
    (tmp_path / "readings").mkdir()  # no ordinal subdirs -> not a section
    (tmp_path / "SYLLABUS.md").write_text("x")  # a file, not a dir
    assert utils.discover_sections(tmp_path) == ["labs", "lectures"]


def test_discover_sections_missing_root_returns_empty(tmp_path):
    assert utils.discover_sections(tmp_path / "nope") == []


def _blob_sha(content: bytes) -> str:
    """What GitHub reports as a file's `.sha` - git's blob hash of its bytes."""
    import hashlib

    return hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content
    ).hexdigest()


def test_put_file_skips_the_write_when_the_content_is_identical(monkeypatch):
    # Refresh re-pushes every seeded file nightly, so an unchanged file must cost nothing:
    # the SHA already fetched for the update is git's blob sha, and comparing it locally
    # keeps a no-change night from filling every org's history with empty commits.
    content = b"name: onboard\n"
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return (0, _blob_sha(content))

    monkeypatch.setattr(utils, "gh", fake_gh)
    assert utils.put_file("org", "repo", "x.yml", content, "ci: seed x") is True
    assert len(calls) == 1  # the SHA read only - no PUT


def test_put_file_writes_with_the_fetched_sha_when_the_content_differs(monkeypatch):
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return (0, _blob_sha(b"something else")) if len(calls) == 1 else (0, "")

    monkeypatch.setattr(utils, "gh", fake_gh)
    assert utils.put_file("org", "repo", "x.yml", b"new\n", "ci: seed x") is True
    assert len(calls) == 2
    assert f"sha={_blob_sha(b'something else')}" in calls[1]


def test_put_files_commits_nothing_when_every_file_already_matches(monkeypatch):
    # The whole point of batching: the nightly refresh re-pushes every generated file at
    # every org, and an unchanged night must still cost zero commits - and now, one read.
    # The guarantee has to survive the move to the git data API, which would otherwise
    # happily commit an empty tree every single night.
    files = {"a.yml": b"one\n", "b.yml": b"two\n"}
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        if args[1] == "repos/org/repo":
            return 0, "main\n"
        return 0, "\n".join(
            ["false", *(f"{p}\t{_blob_sha(c)}" for p, c in files.items())]
        )

    monkeypatch.setattr(utils, "gh", fake_gh)
    assert utils.put_files("org", "repo", files, "ci: refresh") is True
    # The branch read and ONE recursive tree read - not one read per path, which is what
    # made the old shape cost 19 calls a night per course org to discover nothing.
    assert len(calls) == 2
    assert "recursive=1" in calls[1][1]


def test_put_files_lands_every_change_in_one_commit(monkeypatch):
    # One tree, one commit, one ref move - two changed files and a retired one must not
    # become three commits.
    files = {"a.yml": b"one\n", "b.yml": b"two\n"}
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append((args, kwargs.get("stdin")))
        url = args[1]
        if url == "repos/org/repo":
            return 0, "main\n"
        if "git/trees/main" in url:  # every path exists, none matches
            # "false" first: the jq asks for `truncated` ahead of the entries.
            return 0, "false\na.yml\tstale\nb.yml\tstale\nold.yml\tstale"
        if url == "repos/org/repo/commits/main":
            return 0, "head-sha\tbase-tree-sha\n"
        return 0, "new-sha\n"

    monkeypatch.setattr(utils, "gh", fake_gh)
    assert (
        utils.put_files("org", "repo", files, "ci: refresh", delete=["old.yml"]) is True
    )
    posted = [json.loads(stdin) for args, stdin in calls if stdin]
    tree = posted[0]["tree"]
    assert posted[0]["base_tree"] == "base-tree-sha"
    assert {entry["path"] for entry in tree} == {"a.yml", "b.yml", "old.yml"}
    # A null sha is the trees API's spelling of "remove this path".
    assert [e["path"] for e in tree if e.get("sha") is None and "content" not in e] == [
        "old.yml"
    ]
    assert posted[1] == {
        "message": "ci: refresh",
        "tree": "new-sha",
        "parents": ["head-sha"],
    }
    # Exactly one ref move, so exactly one commit lands.
    assert sum(1 for args, _ in calls if "PATCH" in args) == 1


def test_put_files_refuses_to_commit_when_the_tree_cannot_be_read(monkeypatch):
    # An unreadable tree is NOT an empty one. Treating a 403/rate-limit as "nothing is
    # there" would rewrite every file over whatever is actually live, and report retired
    # files as removed while they survive - so the whole commit is abandoned instead.
    monkeypatch.setattr(
        utils, "gh", lambda *a, **k: (1, "gh: Bad credentials (HTTP 401)")
    )
    assert utils.put_files("org", "repo", {"a.yml": b"one\n"}, "ci: refresh") is False


def test_put_files_seeds_a_repo_that_has_no_commits_yet(monkeypatch):
    # create_repo does not auto-init, so the FIRST seed after it lands in a repo with no
    # commit, no tree and no ref. Such a repo refuses `POST /git/trees` OUTRIGHT - "Git
    # Repository is empty" (409) - whether or not a base_tree is sent: the git data API
    # needs a commit to hang a tree off, and only the Contents API will create that first
    # one. This test used to assert the opposite (that omitting base_tree was enough), with
    # a stub that let the POST succeed - so the real 409 went unnoticed until the first
    # cohort org bootstrapped after the classroom-config scaffolds were batched, whose
    # roster, schedule and people.yml never landed at all.
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append((args, kwargs.get("stdin")))
        url = args[1]
        if url == "repos/org/repo":
            return 0, "main\n"
        # every git-data read AND write says the same thing on an empty repo
        if "git/trees" in url or url == "repos/org/repo/commits/main":
            return 1, "gh: Git Repository is empty. (HTTP 409)"
        if url == "repos/org/repo/contents/a.yml" and "--method" not in args:
            return 1, "gh: Not Found (HTTP 404)"  # nothing there to update
        return 0, "new-sha\n"

    monkeypatch.setattr(utils, "gh", fake_gh)
    assert utils.put_files("org", "repo", {"a.yml": b"one\n"}, "init: seed") is True
    # the write went through Contents, which creates the initial commit itself
    assert any(
        "PUT" in args and "repos/org/repo/contents/a.yml" in args for args, _ in calls
    )
    # and no tree was POSTed: the recursive tree READ still happens (it is how we learn the
    # repo is empty), but building one 409s and must not be attempted.
    assert not any(
        "POST" in args and any(a.endswith("/git/trees") for a in args)
        for args, _ in calls
    ), "POST /git/trees 409s on an empty repo - it must not be attempted"


def test_put_files_still_commits_when_only_the_deletion_is_outstanding(monkeypatch):
    # Retiring a workflow whose content files are already current must still produce the
    # one commit that removes it - the "nothing changed" shortcut looks at deletions too.
    content = b"one\n"
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append((args, kwargs.get("stdin")))
        url = args[1]
        if url == "repos/org/repo":
            return 0, "main\n"
        if "git/trees/main" in url:
            return 0, f"false\na.yml\t{_blob_sha(content)}\nold.yml\tstill-here"
        if url == "repos/org/repo/commits/main":
            return 0, "head-sha\tbase-tree-sha\n"
        return 0, "new-sha\n"

    monkeypatch.setattr(utils, "gh", fake_gh)
    assert (
        utils.put_files(
            "org", "repo", {"a.yml": content}, "ci: refresh", delete=["old.yml"]
        )
        is True
    )
    tree = next(json.loads(stdin) for args, stdin in calls if stdin)["tree"]
    assert [entry["path"] for entry in tree] == ["old.yml"]


def test_put_files_skips_a_deletion_of_a_file_that_is_already_gone(monkeypatch):
    # delete= is passed unconditionally on every refresh, long after the retired file went.
    # Absent from the tree means the job is done, not that a commit is owed.
    content = b"one\n"

    def fake_gh(*args, **kwargs):
        if args[1] == "repos/org/repo":
            return 0, "main\n"
        if "git/trees/main" in args[1]:
            return 0, f"false\na.yml\t{_blob_sha(content)}"
        raise AssertionError("nothing to do - no commit legs should run")

    monkeypatch.setattr(utils, "gh", fake_gh)
    assert (
        utils.put_files(
            "org", "repo", {"a.yml": content}, "ci: refresh", delete=["old.yml"]
        )
        is True
    )


def test_repo_is_archived_reads_the_flag_and_assumes_live_when_it_cannot(monkeypatch):
    # This gates whether the nightly refresh skips a cohort, so the failure default is the
    # whole point: an unreadable repo must read as LIVE. Guessing "archived" on a transient
    # error would silently stop converging a running cohort with nothing in the log to say
    # so; guessing "live" costs a loud 403 from the write itself, which is the right alarm.
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, "true\n"))
    assert utils.repo_is_archived("Cohort-f2025", "classroom-config") is True
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, "false\n"))
    assert utils.repo_is_archived("Cohort-f2026", "classroom-config") is False
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: HTTP 502 - bad gateway"))
    assert utils.repo_is_archived("Cohort-f2026", "classroom-config") is False


def test_get_file_content_returns_none_only_for_a_genuine_404(monkeypatch):
    # None is what every caller reads as "not configured yet" (an unseeded roster, an
    # empty cohort registry), so only a real 404 may produce it - a rate-limited or
    # forbidden read has to be loud, or a transient failure looks like an empty course.
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert utils.get_file_content("Org", "classroom-config", "students.csv") is None
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: HTTP 403 - rate limited"))
    with pytest.raises(RuntimeError, match="Org/classroom-config/students.csv"):
        utils.get_file_content("Org", "classroom-config", "students.csv")


def test_get_file_content_returns_the_decoded_body(monkeypatch):
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, "handle,email\n"))
    assert utils.get_file_content("Org", "repo", "students.csv") == "handle,email\n"


def test_gh_always_returns_a_pair(monkeypatch):
    # The retry loop is gh's only return path, so a negative `retries` (no attempt at all)
    # used to fall off the end and hand back None - which every caller unpacks.
    code, out = utils.gh("api", "user", retries=-1)
    assert code != 0 and out


def test_gh_json_names_the_command_it_failed_to_run(monkeypatch):
    # This message is what a CLI prints in an Actions log instead of a traceback, so
    # "gh command failed" on its own leaves nothing to act on.
    import subprocess

    class Result:
        returncode = 1
        stdout = ""
        stderr = "HTTP 403: rate limited"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(RuntimeError, match="`gh search repos topic:dsl-course-hub`"):
        utils.gh_json("search", "repos", "topic:dsl-course-hub")


def test_get_org_owners_distinguishes_no_owners_from_an_unreadable_list(monkeypatch):
    # An empty frozenset disables the prune guard in reconcile_team_members, so a failed
    # read must NOT produce one - it produces None, which skips pruning altogether.
    utils.get_org_owners.cache_clear()
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: HTTP 502"))
    assert utils.get_org_owners("Org") is None
    utils.get_org_owners.cache_clear()
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, "[]"))
    assert utils.get_org_owners("Org") == frozenset()
    utils.get_org_owners.cache_clear()


def test_reconcile_team_members_adds_missing_and_removes_extra(monkeypatch):
    monkeypatch.setattr(utils, "get_team_members", lambda org, team: {"alice", "bob"})
    monkeypatch.setattr(utils, "_acting_login", lambda: None)
    monkeypatch.setattr(utils, "get_org_owners", lambda org: frozenset())
    added, removed = [], []
    monkeypatch.setattr(
        utils,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        utils, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = utils.reconcile_team_members("org", "instructors", {"alice", "carol"})
    assert errors == 0
    assert added == ["carol"]
    assert removed == ["bob"]


def test_reconcile_team_members_never_prunes_the_acting_login(monkeypatch):
    monkeypatch.setattr(
        utils, "get_team_members", lambda org, team: {"alice", "hertie-dsl-bot"}
    )
    monkeypatch.setattr(utils, "_acting_login", lambda: "hertie-dsl-bot")
    monkeypatch.setattr(utils, "get_org_owners", lambda org: frozenset())
    removed = []
    monkeypatch.setattr(utils, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(
        utils, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = utils.reconcile_team_members("org", "course-admin", wanted=set())
    assert errors == 0
    assert removed == ["alice"]


def test_reconcile_team_members_never_prunes_any_org_owner(monkeypatch):
    # The robust fix: exclude ALL owners, not just whoever's currently running the
    # sync - so a human running this locally doesn't evict the bot (or vice versa).
    monkeypatch.setattr(
        utils,
        "get_team_members",
        lambda org, team: {"alice", "hertie-dsl-bot", "henrycgbaker"},
    )
    monkeypatch.setattr(
        utils, "_acting_login", lambda: "henrycgbaker"
    )  # a human, running locally
    monkeypatch.setattr(
        utils,
        "get_org_owners",
        lambda org: frozenset({"hertie-dsl-bot", "henrycgbaker"}),
    )
    removed = []
    monkeypatch.setattr(utils, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(
        utils, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = utils.reconcile_team_members("org", "course-admin", wanted=set())
    assert errors == 0
    assert removed == ["alice"]  # neither owner touched, despite neither being declared


def test_reconcile_team_members_compares_case_insensitively(monkeypatch, capsys):
    # GitHub logins are case-insensitive: a hand-typed `Anna-Adams` and the API's
    # `anna-adams` are the same account. Comparing raw casing added-then-pruned it every
    # run, oscillating that person's access nightly.
    monkeypatch.setattr(utils, "get_team_members", lambda org, team: {"anna-adams"})
    monkeypatch.setattr(utils, "_acting_login", lambda: None)
    monkeypatch.setattr(utils, "get_org_owners", lambda org: frozenset())
    added, removed = [], []
    monkeypatch.setattr(
        utils,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        utils, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = utils.reconcile_team_members("org", "instructors", {"Anna-Adams"})
    assert errors == 0
    assert added == []  # already present (case-folded) - not re-added
    assert removed == []  # ...and therefore not pruned as "unwanted"


def test_reconcile_team_members_aborts_when_current_membership_is_unreadable(
    monkeypatch, capsys
):
    # get_team_members returns None when the team's membership can't be read. Adding or
    # pruning blind against it is unsafe, so the whole reconcile aborts with an error -
    # it must not treat the team as empty (which would re-add everyone, or prune nobody).
    monkeypatch.setattr(utils, "get_team_members", lambda org, team: None)
    added, removed = [], []
    monkeypatch.setattr(
        utils,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        utils, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = utils.reconcile_team_members("org", "instructors", {"alice"})
    assert errors == 1
    assert added == [] and removed == []
    assert "reconcile aborted" in capsys.readouterr().err


def test_get_team_members_returns_none_on_failure_not_an_empty_set(monkeypatch):
    # None (unreadable) must never be conflated with an empty team, or a reconcile acts
    # blind. Mirrors get_org_owners.
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: HTTP 502"))
    assert utils.get_team_members("Org", "students") is None
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, "not json"))
    assert utils.get_team_members("Org", "students") is None
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, '[{"login": "alice"}]'))
    assert utils.get_team_members("Org", "students") == {"alice"}


def test_active_today_accepts_date_objects_as_bounds():
    # An unquoted `start: 2026-09-01` in people.yml parses to a datetime.date, not a
    # string; `today < start` used to raise TypeError: str < date.
    from datetime import date, datetime

    assert utils.active_today(date(2026, 9, 1), None, "2026-10-01") is True
    assert utils.active_today(date(2026, 11, 1), None, "2026-10-01") is False
    assert utils.active_today(None, date(2026, 9, 30), "2026-10-01") is False
    assert utils.active_today(None, date(2026, 12, 31), "2026-10-01") is True
    # a full datetime (date subclass) is sliced back to its date portion
    assert utils.active_today(datetime(2026, 9, 1, 12, 0), None, "2026-10-01") is True
    # strings still work exactly as before
    assert utils.active_today("2026-09-01", "2026-12-31", "2026-10-01") is True


def test_load_yaml_config_distinguishes_absent_empty_and_malformed(monkeypatch):
    import yaml

    # ABSENT (404 -> get_file_content None) -> None: pruning callers must not treat this
    # as an empty desired set.
    monkeypatch.setattr(utils, "get_file_content", lambda *a, **k: None)
    assert utils.load_yaml_config("Org", ".github", "dsl-course.yml") is None

    # present but empty -> {} (a legitimate "empty the team")
    monkeypatch.setattr(utils, "get_file_content", lambda *a, **k: "")
    assert utils.load_yaml_config("Org", ".github", "dsl-course.yml") == {}

    # present with content -> the parsed mapping
    monkeypatch.setattr(utils, "get_file_content", lambda *a, **k: "people:\n  x: 1\n")
    assert utils.load_yaml_config("Org", ".github", "dsl-course.yml") == {
        "people": {"x": 1}
    }

    # malformed YAML -> logged + raised, never silently {}
    monkeypatch.setattr(utils, "get_file_content", lambda *a, **k: "a: b: c\n")
    with pytest.raises(yaml.YAMLError):
        utils.load_yaml_config("Org", ".github", "dsl-course.yml")

    # a non-mapping top level (list/scalar) -> raised, naming the file
    monkeypatch.setattr(utils, "get_file_content", lambda *a, **k: "- a\n- b\n")
    with pytest.raises(RuntimeError, match="not a YAML mapping"):
        utils.load_yaml_config("Org", ".github", "dsl-course.yml")


def test_load_yaml_config_propagates_a_non_404_read_error(monkeypatch):
    # get_file_content raises on any non-404 failure; load_yaml_config must not swallow it
    # into None/{}, or a transient error reads as "not configured".
    def boom(*a, **k):
        raise RuntimeError("could not read Org/.github/dsl-course.yml: HTTP 403")

    monkeypatch.setattr(utils, "get_file_content", boom)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        utils.load_yaml_config("Org", ".github", "dsl-course.yml")


def test_create_repo_only_treats_a_genuine_name_clash_422_as_success(monkeypatch):
    # A bare `"422" in out` swallowed an invalid-name/policy 422 as success, so the caller
    # then wrote into a repo that was never created. Only the name-clash message is success.
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, ""))
    assert utils.create_repo("Org", "good") is True
    monkeypatch.setattr(
        utils,
        "gh",
        lambda *a, **k: (
            1,
            "HTTP 422: Validation Failed - name already exists on this account",
        ),
    )
    assert utils.create_repo("Org", "dup") is True
    monkeypatch.setattr(
        utils,
        "gh",
        lambda *a, **k: (1, "HTTP 422: Validation Failed - name is invalid"),
    )
    assert utils.create_repo("Org", "bad name") is False


def test_create_team_only_treats_an_already_exists_422_as_success(monkeypatch):
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, ""))
    assert utils.create_team("Org", "students") is True
    monkeypatch.setattr(
        utils, "gh", lambda *a, **k: (1, "HTTP 422: name already_exists")
    )
    assert utils.create_team("Org", "students") is True
    # The body GitHub's teams endpoint ACTUALLY returns for a duplicate team, verbatim.
    # It says neither "already exists" nor `already_exists`, so it read as a hard failure
    # and every membership sync after a team's first creation died on it.
    duplicate_team_422 = (
        '{"message":"Validation Failed","errors":[{"resource":"Team",'
        '"code":"unprocessable","field":"data",'
        '"message":"Name must be unique for this org"}],'
        '"documentation_url":"https://docs.github.com/rest/teams..."}'
    )
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, duplicate_team_422))
    assert utils.create_team("Org", "students") is True
    monkeypatch.setattr(
        utils, "gh", lambda *a, **k: (1, "HTTP 422: Validation Failed - name too long")
    )
    assert utils.create_team("Org", "x" * 200) is False


def test_is_valid_github_username_charset_and_hyphen_rules():
    assert utils.is_valid_github_username("anna-adams")
    assert utils.is_valid_github_username("Anna-Adams")
    assert utils.is_valid_github_username("a" * 39)
    assert not utils.is_valid_github_username("a" * 40)  # too long
    assert not utils.is_valid_github_username("-anna")  # leading hyphen
    assert not utils.is_valid_github_username("anna-")  # trailing hyphen
    assert not utils.is_valid_github_username("an--na")  # double hyphen
    assert not utils.is_valid_github_username("a_b")  # underscore not allowed
    assert not utils.is_valid_github_username("")


def test_reconcile_team_members_skips_the_prune_when_the_owners_are_unreadable(
    monkeypatch, capsys
):
    # Without the owner list there is no way to tell an Owner from a stray member, and a
    # blind prune could evict one. Adds still happen; the prune pass is skipped, loudly.
    monkeypatch.setattr(utils, "get_team_members", lambda org, team: {"alice"})
    monkeypatch.setattr(utils, "_acting_login", lambda: None)
    monkeypatch.setattr(utils, "get_org_owners", lambda org: None)
    added, removed = [], []
    monkeypatch.setattr(
        utils,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        utils, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = utils.reconcile_team_members("org", "course-admin", {"carol"})
    assert errors == 0
    assert added == ["carol"]
    assert removed == []
    assert "pruning skipped" in capsys.readouterr().err


def test_repo_missing_is_true_only_on_a_404(monkeypatch):
    # `repo_exists` is optimistic (any failure = absent) because it answers a create-if-
    # missing question. `repo_missing` answers "may I record something permanent on the
    # strength of absence?" - so a 5xx or a rate limit is neither present nor absent.
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert utils.repo_missing("O", "r")
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "HTTP 502 bad gateway"))
    assert not utils.repo_missing("O", "r")
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (0, "{}"))
    assert not utils.repo_missing("O", "r")


# ------------------------------- per-person lines stay out of a world-readable log


def test_log_verbose_is_silent_unless_dsl_verbose_is_set(capsys, monkeypatch):
    # Every faculty workflow runs in the course org's PUBLIC .github, so a line naming a
    # student may only appear when a human asks for it on their own machine.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    utils.log_verbose("@ada-l")
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("DSL_VERBOSE", "1")
    utils.log_verbose("@ada-l")
    assert "@ada-l" in capsys.readouterr().out


def test_an_empty_dsl_verbose_is_not_verbose(capsys, monkeypatch):
    monkeypatch.setenv("DSL_VERBOSE", "")
    utils.log_verbose("@ada-l")
    assert capsys.readouterr().out == ""


def test_reconcile_keeps_the_handles_it_adds_and_removes_out_of_a_public_log(
    capsys, monkeypatch
):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(utils, "get_team_members", lambda org, team: {"zoe-zed"})
    monkeypatch.setattr(utils, "_acting_login", lambda: "bot")
    monkeypatch.setattr(utils, "get_org_owners", lambda org: frozenset())
    monkeypatch.setattr(utils, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(utils, "remove_team_member", lambda *a, **k: True)
    assert utils.reconcile_team_members("org", "students", {"ada-l"}, prune=True) == 0
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out and "zoe-zed" not in captured.out


# ------------------------- a read-modify-write must not clobber a concurrent commit


def _record_gh(monkeypatch, answers):
    """Stub utils.gh with a queue of (code, out) answers; returns the arg tuples seen."""
    queue = list(answers)
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return queue.pop(0) if queue else (0, "")

    monkeypatch.setattr(utils, "gh", fake_gh)
    return calls


def test_put_file_sends_the_sha_the_caller_read_without_re_reading(monkeypatch):
    # The bug: put_file fetched the sha immediately before writing, so the write succeeded
    # however stale its content was - and a commit that landed in between was reverted.
    calls = _record_gh(monkeypatch, [(0, "")])
    assert utils.put_file(
        "O", "R", "students.csv", b"new", "msg", expected_sha="abc123"
    )
    assert len(calls) == 1  # the write only - no fresh read to race against
    assert "sha=abc123" in calls[0]


def test_put_file_with_an_expected_sha_reports_a_refused_write(monkeypatch, capsys):
    _record_gh(monkeypatch, [(1, "HTTP 409: is at ... but expected abc123")])
    assert not utils.put_file(
        "O", "R", "students.csv", b"new", "msg", expected_sha="abc123"
    )
    assert "failed to put students.csv" in capsys.readouterr().err


def test_put_file_skips_a_write_that_would_change_nothing(monkeypatch):
    calls = _record_gh(monkeypatch, [])
    content = b"unchanged"
    assert utils.put_file(
        "O", "R", "f", content, "msg", expected_sha=utils.blob_sha(content)
    )
    assert calls == []  # no read, no write


def test_put_file_without_an_expected_sha_still_reads_then_writes(monkeypatch):
    calls = _record_gh(monkeypatch, [(0, "livesha"), (0, "")])
    assert utils.put_file("O", "R", "f", b"new", "msg")
    assert len(calls) == 2 and "sha=livesha" in calls[1]


def test_get_file_with_sha_splits_the_sha_off_the_content(monkeypatch):
    _record_gh(monkeypatch, [(0, "abc123\nname,email\nAda,a@x.edu")])
    assert utils.get_file_with_sha("O", "R", "students.csv") == (
        "name,email\nAda,a@x.edu",
        "abc123",
    )


def test_get_file_with_sha_is_none_only_for_a_genuine_404(monkeypatch):
    _record_gh(monkeypatch, [(1, "gh: Not Found (HTTP 404)")])
    assert utils.get_file_with_sha("O", "R", "students.csv") is None
    _record_gh(monkeypatch, [(1, "HTTP 403: rate limited")])
    with pytest.raises(RuntimeError, match="could not read"):
        utils.get_file_with_sha("O", "R", "students.csv")


# ------------------------- a truncated tree listing is not a smaller repo


def test_repo_tree_raises_when_github_truncated_the_listing(monkeypatch):
    # The git-tree API caps a recursive listing and says so in `truncated: true` rather
    # than failing. Believed, a partial listing looks exactly like a smaller repo - the
    # site drops the material links it never saw, and put_files rewrites what it thinks
    # is missing.
    _record_gh(monkeypatch, [(0, "true\nlectures/01_intro/notes.pdf")])
    with pytest.raises(utils.TruncatedTree, match="TRUNCATED"):
        utils.repo_tree("O", "R", "main")


def test_repo_blob_shas_raises_when_github_truncated_the_listing(monkeypatch):
    _record_gh(monkeypatch, [(0, "true\na.yml\tsha1")])
    with pytest.raises(utils.TruncatedTree):
        utils.repo_blob_shas("O", "R", "main")


def test_an_untruncated_tree_drops_the_flag_line(monkeypatch):
    _record_gh(monkeypatch, [(0, "false\nb.md\na.md")])
    assert utils.repo_tree("O", "R", "main") == ("a.md", "b.md")
    _record_gh(monkeypatch, [(0, "false\na.yml\tsha1")])
    assert utils.repo_blob_shas("O", "R", "main") == {"a.yml": "sha1"}


# --------------------------------------------------- git must not hang for six hours


def test_a_hung_git_comes_back_as_an_ordinary_failure(monkeypatch):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", timeout)
    code, out = utils.git("clone", "https://example.invalid/r")
    assert code == 1  # every caller's `!= 0` check reports it; no exception escapes
    assert "timed out" in out


def test_git_passes_a_timeout_to_the_subprocess(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)

        class R:
            returncode, stdout, stderr = 0, "", ""

        return R()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    utils.git("status")
    assert seen["timeout"] == utils.GIT_TIMEOUT_SECONDS
