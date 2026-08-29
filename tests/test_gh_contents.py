"""Reading and writing repo files: the single-file put with its concurrent-write
guard, the whole-tree commit, and the readers that must tell an absent file from an
unreadable one."""

from __future__ import annotations

import json

import pytest

from dsl_course import gh_contents, repos

# What `GET repos/{org}/{name}` answers. `repos._repo` reads the whole object once and
# every question about the repo - default branch included - is asked of that.
_REPO_OBJECT = '{"default_branch": "main", "private": true, "archived": false}'


def _stub_gh(monkeypatch, fake):
    """One fake for both `gh` bindings a write path reads: `put_files` asks `repos` for the
    default branch before it asks this module for the tree."""
    monkeypatch.setattr(gh_contents, "gh", fake)
    monkeypatch.setattr(repos, "gh", fake)


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

    _stub_gh(monkeypatch, fake_gh)
    assert gh_contents.put_file("org", "repo", "x.yml", content, "ci: seed x") is True
    assert len(calls) == 1  # the SHA read only - no PUT


def test_put_file_writes_with_the_fetched_sha_when_the_content_differs(monkeypatch):
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return (0, _blob_sha(b"something else")) if len(calls) == 1 else (0, "")

    _stub_gh(monkeypatch, fake_gh)
    assert gh_contents.put_file("org", "repo", "x.yml", b"new\n", "ci: seed x") is True
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
            return 0, _REPO_OBJECT
        return 0, "\n".join(
            ["false", *(f"{p}\t{_blob_sha(c)}" for p, c in files.items())]
        )

    _stub_gh(monkeypatch, fake_gh)
    assert gh_contents.put_files("org", "repo", files, "ci: refresh") is True
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
            return 0, _REPO_OBJECT
        if "git/trees/main" in url:  # every path exists, none matches
            # "false" first: the jq asks for `truncated` ahead of the entries.
            return 0, "false\na.yml\tstale\nb.yml\tstale\nold.yml\tstale"
        if url == "repos/org/repo/commits/main":
            return 0, "head-sha\tbase-tree-sha\n"
        return 0, "new-sha\n"

    _stub_gh(monkeypatch, fake_gh)
    assert (
        gh_contents.put_files("org", "repo", files, "ci: refresh", delete=["old.yml"])
        is True
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
    _stub_gh(monkeypatch, lambda *a, **k: (1, "gh: Bad credentials (HTTP 401)"))
    assert (
        gh_contents.put_files("org", "repo", {"a.yml": b"one\n"}, "ci: refresh")
        is False
    )


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
            return 0, _REPO_OBJECT
        # every git-data read AND write says the same thing on an empty repo
        if "git/trees" in url or url == "repos/org/repo/commits/main":
            return 1, "gh: Git Repository is empty. (HTTP 409)"
        if url == "repos/org/repo/contents/a.yml" and "--method" not in args:
            return 1, "gh: Not Found (HTTP 404)"  # nothing there to update
        return 0, "new-sha\n"

    _stub_gh(monkeypatch, fake_gh)
    assert (
        gh_contents.put_files("org", "repo", {"a.yml": b"one\n"}, "init: seed") is True
    )
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
            return 0, _REPO_OBJECT
        if "git/trees/main" in url:
            return 0, f"false\na.yml\t{_blob_sha(content)}\nold.yml\tstill-here"
        if url == "repos/org/repo/commits/main":
            return 0, "head-sha\tbase-tree-sha\n"
        return 0, "new-sha\n"

    _stub_gh(monkeypatch, fake_gh)
    assert (
        gh_contents.put_files(
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
            return 0, _REPO_OBJECT
        if "git/trees/main" in args[1]:
            return 0, f"false\na.yml\t{_blob_sha(content)}"
        raise AssertionError("nothing to do - no commit legs should run")

    _stub_gh(monkeypatch, fake_gh)
    assert (
        gh_contents.put_files(
            "org", "repo", {"a.yml": content}, "ci: refresh", delete=["old.yml"]
        )
        is True
    )


def test_get_file_content_returns_none_only_for_a_genuine_404(monkeypatch):
    # None is what every caller reads as "not configured yet" (an unseeded roster, an
    # empty cohort registry), so only a real 404 may produce it - a rate-limited or
    # forbidden read has to be loud, or a transient failure looks like an empty course.
    _stub_gh(monkeypatch, lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert (
        gh_contents.get_file_content("Org", "classroom-config", "students.csv") is None
    )
    _stub_gh(monkeypatch, lambda *a, **k: (1, "gh: HTTP 403 - rate limited"))
    with pytest.raises(RuntimeError, match="Org/classroom-config/students.csv"):
        gh_contents.get_file_content("Org", "classroom-config", "students.csv")


def test_get_file_content_returns_the_decoded_body(monkeypatch):
    _stub_gh(monkeypatch, lambda *a, **k: (0, "handle,email\n"))
    assert (
        gh_contents.get_file_content("Org", "repo", "students.csv") == "handle,email\n"
    )


def test_load_yaml_config_distinguishes_absent_empty_and_malformed(monkeypatch):
    import yaml

    # ABSENT (404 -> get_file_content None) -> None: pruning callers must not treat this
    # as an empty desired set.
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: None)
    assert gh_contents.load_yaml_config("Org", ".github", "dsl-course.yml") is None

    # present but empty -> {} (a legitimate "empty the team")
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: "")
    assert gh_contents.load_yaml_config("Org", ".github", "dsl-course.yml") == {}

    # present with content -> the parsed mapping
    monkeypatch.setattr(
        gh_contents, "get_file_content", lambda *a, **k: "people:\n  x: 1\n"
    )
    assert gh_contents.load_yaml_config("Org", ".github", "dsl-course.yml") == {
        "people": {"x": 1}
    }

    # malformed YAML -> logged + raised, never silently {}
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: "a: b: c\n")
    with pytest.raises(yaml.YAMLError):
        gh_contents.load_yaml_config("Org", ".github", "dsl-course.yml")

    # a non-mapping top level (list/scalar) -> raised, naming the file
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: "- a\n- b\n")
    with pytest.raises(RuntimeError, match="not a YAML mapping"):
        gh_contents.load_yaml_config("Org", ".github", "dsl-course.yml")


def test_load_yaml_config_propagates_a_non_404_read_error(monkeypatch):
    # get_file_content raises on any non-404 failure; load_yaml_config must not swallow it
    # into None/{}, or a transient error reads as "not configured".
    def boom(*a, **k):
        raise RuntimeError("could not read Org/.github/dsl-course.yml: HTTP 403")

    monkeypatch.setattr(gh_contents, "get_file_content", boom)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        gh_contents.load_yaml_config("Org", ".github", "dsl-course.yml")


# ------------------------- a read-modify-write must not clobber a concurrent commit


def _record_gh(monkeypatch, answers):
    """Stub gh_contents.gh with a queue of (code, out) answers; returns the arg tuples seen."""
    queue = list(answers)
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return queue.pop(0) if queue else (0, "")

    _stub_gh(monkeypatch, fake_gh)
    return calls


def test_put_file_sends_the_sha_the_caller_read_without_re_reading(monkeypatch):
    # The bug: put_file fetched the sha immediately before writing, so the write succeeded
    # however stale its content was - and a commit that landed in between was reverted.
    calls = _record_gh(monkeypatch, [(0, "")])
    assert gh_contents.put_file(
        "O", "R", "students.csv", b"new", "msg", expected_sha="abc123"
    )
    assert len(calls) == 1  # the write only - no fresh read to race against
    assert "sha=abc123" in calls[0]


def test_put_file_with_an_expected_sha_reports_a_refused_write(monkeypatch, capsys):
    _record_gh(monkeypatch, [(1, "HTTP 409: is at ... but expected abc123")])
    assert not gh_contents.put_file(
        "O", "R", "students.csv", b"new", "msg", expected_sha="abc123"
    )
    assert "failed to put students.csv" in capsys.readouterr().err


def test_put_file_skips_a_write_that_would_change_nothing(monkeypatch):
    calls = _record_gh(monkeypatch, [])
    content = b"unchanged"
    assert gh_contents.put_file(
        "O", "R", "f", content, "msg", expected_sha=gh_contents.blob_sha(content)
    )
    assert calls == []  # no read, no write


def test_put_file_without_an_expected_sha_still_reads_then_writes(monkeypatch):
    calls = _record_gh(monkeypatch, [(0, "livesha"), (0, "")])
    assert gh_contents.put_file("O", "R", "f", b"new", "msg")
    assert len(calls) == 2 and "sha=livesha" in calls[1]


def test_get_file_with_sha_splits_the_sha_off_the_content(monkeypatch):
    _record_gh(monkeypatch, [(0, "abc123\nname,email\nAda,a@x.edu")])
    assert gh_contents.get_file_with_sha("O", "R", "students.csv") == (
        "name,email\nAda,a@x.edu",
        "abc123",
    )


def test_get_file_with_sha_is_none_only_for_a_genuine_404(monkeypatch):
    _record_gh(monkeypatch, [(1, "gh: Not Found (HTTP 404)")])
    assert gh_contents.get_file_with_sha("O", "R", "students.csv") is None
    _record_gh(monkeypatch, [(1, "HTTP 403: rate limited")])
    with pytest.raises(RuntimeError, match="could not read"):
        gh_contents.get_file_with_sha("O", "R", "students.csv")


# ------------------------- a truncated tree listing is not a smaller repo


def test_repo_tree_raises_when_github_truncated_the_listing(monkeypatch):
    # The git-tree API caps a recursive listing and says so in `truncated: true` rather
    # than failing. Believed, a partial listing looks exactly like a smaller repo - the
    # site drops the material links it never saw, and put_files rewrites what it thinks
    # is missing.
    _record_gh(monkeypatch, [(0, "true\nlectures/01_intro/notes.pdf")])
    with pytest.raises(gh_contents.TruncatedTree, match="TRUNCATED"):
        gh_contents.repo_tree("O", "R", "main")


def test_repo_blob_shas_raises_when_github_truncated_the_listing(monkeypatch):
    _record_gh(monkeypatch, [(0, "true\na.yml\tsha1")])
    with pytest.raises(gh_contents.TruncatedTree):
        gh_contents.repo_blob_shas("O", "R", "main")


def test_an_untruncated_tree_drops_the_flag_line(monkeypatch):
    _record_gh(monkeypatch, [(0, "false\nb.md\na.md")])
    assert gh_contents.repo_tree("O", "R", "main") == ("a.md", "b.md")
    _record_gh(monkeypatch, [(0, "false\na.yml\tsha1")])
    assert gh_contents.repo_blob_shas("O", "R", "main") == {"a.yml": "sha1"}
