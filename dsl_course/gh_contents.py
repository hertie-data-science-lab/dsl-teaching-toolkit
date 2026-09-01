"""Reading and writing files in a repo through the Contents and Git Data APIs: single
puts, whole-tree commits, the seeded-stub rules, and the CSV/YAML readers on top.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from collections.abc import Iterable
from typing import Any

import yaml

from .ghcli import gh, is_missing_resource
from .log import log_err, log_skip
from .repos import default_branch


def _require_csv_header(
    fieldnames: list[str] | None, required: tuple[str, ...], what: str
) -> None:
    """Refuse a CSV whose header lacks a column the caller cannot do without.

    The failure this exists for: a German-locale Excel saves `;`-delimited CSV. DictReader
    then sees ONE header column, every field reads "", nothing raises, and the caller
    proceeds on an empty roster / empty marks - `enrol_codes` even wrote such a file back
    mangled, exit 0. A header that cannot name the required columns is a hard error."""
    have = {f.strip() for f in (fieldnames or [])}
    missing = [f for f in required if f not in have]
    if missing:
        raise RuntimeError(
            f"{what}: header lacks {', '.join(missing)} (got {list(fieldnames or [])}). "
            f"A semicolon-delimited export looks like this - save the file as "
            f"comma-separated UTF-8 CSV and try again."
        )


def _strip_bom(text: str) -> str:
    """Drop a leading UTF-8 BOM. Excel exports CSVs with one, and left in place
    `csv.DictReader` reads it into the first header name so every lookup on that column
    misses and rows are silently dropped."""
    return text.lstrip("﻿")


def read_csv(text: str, required: tuple[str, ...], what: str) -> csv.DictReader:
    """A DictReader over `text`, BOM stripped and the header checked for `required`.

    The single door every HAND-EDITED CSV comes through - the roster, teams.csv, a grades
    CSV, the enrol-code writers. Excel's two ways of handing back an unreadable file (a
    leading BOM, a `;`-delimited export) each look like ordinary empty data to DictReader,
    so neither is optional and neither is left to a caller to remember. `what` names the
    file in the error."""
    reader = csv.DictReader(io.StringIO(_strip_bom(text)))
    _require_csv_header(reader.fieldnames, required, what)
    return reader


def dump_csv(header: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    """`header` plus `rows` as CSV text - the write half of read_csv, so every CSV this
    toolkit generates is written by one serialiser."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def file_exists(org: str, repo: str, path: str) -> bool:
    """Whether `path` is present in `org/repo`, in one Contents probe.

    For a FIRE-ONCE marker, where the only question is "has this already happened". Any
    failure to read reads as absent, which costs a re-run rather than a missed one -
    unlike get_file_content, whose callers act on the CONTENT and must never take a rate
    limit for an empty file."""
    code, _ = gh("api", f"repos/{org}/{repo}/contents/{path}", "--jq", ".sha")
    return code == 0


def blob_sha(content: bytes) -> str:
    """Git's blob hash of `content` - what the Contents API reports as a file's `.sha`.

    Computing it locally is what lets every writer here decide, with no extra API call,
    whether a write would change anything."""
    return hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content
    ).hexdigest()


def put_file(
    org: str,
    repo: str,
    path: str,
    content: bytes,
    message: str,
    expected_sha: str | None = None,
) -> bool:
    """Create or update a file via the Contents API.

    Updates require the existing file's SHA. By default it is fetched here, immediately
    before the write. That SHA is git's blob sha, so comparing it with the blob sha of
    `content` computed locally tells us - with no extra API call - whether the write would
    change anything: an identical file is left alone. Callers may therefore run on a
    schedule without filling repos with no-op commits.

    `expected_sha` is for a read-modify-write: pass the sha the content was READ at (see
    get_file_with_sha) and that sha is sent as-is, no fresh read. GitHub then REFUSES the
    write if the file has moved on since - which is the whole point. Re-reading the sha at
    write time makes the API call succeed however stale the content is, so a commit that
    landed between the read and the write is silently reverted; that is how Send codes
    could wipe a Join binding out of students.csv. A caller passing it must be ready to
    re-read, re-apply its change, and retry.

    One file, one commit. Use put_files when several files belong in the SAME commit.
    """
    b64 = base64.b64encode(content).decode()
    args = [
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{repo}/contents/{path}",
        "--field",
        f"message={message}",
        "--field",
        f"content={b64}",
    ]
    if expected_sha is not None:
        if expected_sha == blob_sha(content):
            return True  # the file already holds exactly this
        if expected_sha:
            args += ["--field", f"sha={expected_sha}"]
    else:
        # If the file already exists, fetch its SHA (required for update)
        code, sha = gh(
            "api",
            f"repos/{org}/{repo}/contents/{path}",
            "--jq",
            ".sha",
        )
        if code == 0 and sha:
            if sha == blob_sha(content):
                return True
            args += ["--field", f"sha={sha}"]
    code, out = gh(*args)
    if code == 0:
        return True
    log_err(f"failed to put {path}: {out[:200]}")
    return False


class TruncatedTree(RuntimeError):
    """A recursive git-tree listing GitHub had to cut short."""


def _untruncated(out: str, org: str, repo: str) -> list[str]:
    """The tree listing's path lines, having first checked the `truncated` flag it carries
    on its FIRST line.

    The git-tree API caps a recursive listing (100k entries / 7MB) and says so in
    `truncated: true` rather than failing. Read past it, a partial listing looks exactly
    like a smaller repo - so a site sync drops the material links it did not see, and
    put_files rewrites the files it thinks are missing. Both callers here are the fail-loud
    kind (see their docstrings), so this is one more way the answer can be untrustworthy
    and must raise rather than be believed."""
    lines = out.splitlines()
    if lines and lines[0].strip() == "true":
        raise TruncatedTree(
            f"the recursive git tree of {org}/{repo} came back TRUNCATED - GitHub could "
            f"not list every path, and acting on a partial listing would delete or "
            f"rewrite whatever it left out. Split the repo, or read it per directory."
        )
    return lines[1:] if lines else []


def _tree(org: str, repo: str, branch: str, jq: str) -> list[str]:
    """The lines of ONE recursive git-tree fetch, `truncated` already checked.

    `[]` means the tree is genuinely empty: a 404 (no such repo/branch) or a 409 (a repo
    with no commits yet - the state every repo is in between create_repo and its first
    seed). Any OTHER failure RAISES rather than reporting an empty tree, the same rule as
    get_file_content: swallowed, an unreadable tree reads as "nothing is there", and the
    caller then rewrites the files it could not see or drops the links it never found."""
    code, out = gh(
        "api", f"repos/{org}/{repo}/git/trees/{branch}?recursive=1", "--jq", jq
    )
    if code != 0:
        if is_missing_resource(out) or "HTTP 409" in out:
            return []
        raise RuntimeError(f"could not read the tree of {org}/{repo}: {out[:200]}")
    return _untruncated(out, org, repo)


def repo_blob_shas(org: str, repo: str, branch: str) -> dict[str, str]:
    """`{path: blob sha}` for every file in `org/repo`'s `branch` - ONE recursive fetch.

    The sha-carrying twin of repo_tree (which answers "which paths exist" for discovery).
    One call answers "what is currently there" for a whole set of paths at once, which is
    what lets put_files decide a no-op night for twenty files without twenty reads."""
    lines = _tree(
        org,
        repo,
        branch,
        r'"\(.truncated)", (.tree[] | select(.type=="blob") | [.path, .sha] | @tsv)',
    )
    entries = (line.split("\t") for line in lines if "\t" in line)
    return {path: sha for path, sha in entries}


def put_files(
    org: str,
    repo: str,
    files: dict[str, bytes],
    message: str,
    *,
    delete: Iterable[str] = (),
    create_only: bool = False,
) -> bool:
    """Write `files` and remove `delete` in a SINGLE commit, via the git data API.

    put_file is one commit per file, which is right for a lone write but turns a set of
    files that always change together - the generated workflows - into a burst of
    near-identical commits in a repo faculty read. This makes that one commit.

    Same no-op guarantee as put_file, for ONE read rather than one per path: a single
    recursive tree fetch gives every live blob sha, and a path whose sha already matches
    the content we would write is dropped, as is a `delete` path already absent. When
    nothing survives that filter there is NO commit at all - so the nightly refresh, which
    re-pushes every generated file at every org, stays both silent and cheap. The tree,
    commit and ref calls are paid only on a run that genuinely changes something.

    `create_only=True` is THE create-only write for a SET of files - `seed_if_absent`'s
    twin, and the USER-owned rule: a path that already exists is left exactly as faculty
    left it and logged as a skip, so a repair re-run's output still shows what it left
    alone. Only the genuinely absent paths are written, and they still go together,
    because a scaffold set is one act of seeding rather than six.

    `files` values are text (workflow YAML, markdown, CSV headers); they go into the tree
    as strings, which is what the trees API takes.

    Returns False if the tree could not be read, or if any leg of the commit failed - a
    partial write is impossible here, since the ref only moves once the whole tree is
    built."""
    try:
        branch = default_branch(org, repo)
        live = repo_blob_shas(org, repo, branch)
    except RuntimeError as exc:
        log_err(str(exc))
        return False
    tree: list[dict[str, Any]] = []
    for path, content in files.items():
        if create_only and path in live:
            log_skip(f"{repo}/{path}")
            continue
        if live.get(path) != blob_sha(content):
            tree.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "content": content.decode(),
                }
            )
    for path in delete:
        if path in live:
            # A null sha is how the trees API spells "remove this path".
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
    if not tree:
        return True
    return _commit_tree(org, repo, branch, tree, message)


def _head(org: str, repo: str, branch: str) -> tuple[str, str] | None:
    """`(head sha, its tree sha)` for `branch`, or None if the repo has NO commits yet.

    Empty is a state every repo passes through: create_repo does not auto-init, so the
    first seed after it lands into a repo with no commit, no tree and no ref. The Contents
    API hides that - it creates the initial commit itself - and the git data API cannot be
    told: see `_commit_tree`, which routes that one case back through Contents."""
    code, out = gh(
        "api",
        f"repos/{org}/{repo}/commits/{branch}",
        "--jq",
        "[.sha, .commit.tree.sha] | @tsv",
    )
    if code == 0 and len(out.split()) == 2:
        head, base_tree = out.split()
        return head, base_tree
    if is_missing_resource(out) or "HTTP 409" in out:
        return None
    raise RuntimeError(f"could not read {org}/{repo}@{branch}: {out[:200]}")


def _seed_first_commit(
    org: str, repo: str, tree: list[dict[str, Any]], message: str
) -> bool:
    """The first commit into a repo that has none, via the Contents API - the only one that
    will create it (see `_commit_tree`).

    One commit per file rather than one for the set, which is the cost of the API that
    works here; it is paid once in a repo's life, on the seed that creates it. Deletions in
    `tree` are skipped: there is nothing in an empty repo to remove."""
    ok = True
    for entry in tree:
        content = entry.get("content")
        if content is None:  # a `sha: None` delete - nothing there to delete
            continue
        if not put_file(org, repo, entry["path"], content.encode(), message):
            ok = False
    return ok


def _commit_tree(
    org: str, repo: str, branch: str, tree: list[dict[str, Any]], message: str
) -> bool:
    """Land `tree` (entries relative to `branch`'s current tree) as one commit.

    The ref update is deliberately NOT forced: if a concurrent run moved the branch since
    we read its head, GitHub rejects the fast-forward and we report a failure the caller
    counts, rather than silently discarding whatever landed in between."""
    try:
        parent = _head(org, repo, branch)
    except RuntimeError as exc:
        log_err(str(exc))
        return False
    if parent is None:
        # A repo with NO commits at all refuses `POST /git/trees` outright - "Git Repository
        # is empty" (409) - whether or not a base_tree is sent. Omitting base_tree is not
        # enough: the git data API needs a commit to hang a tree off, and only the Contents
        # API will create that first one. So the first write into a freshly-created repo
        # goes file by file through Contents, and every later write takes the batched path.
        #
        # This is not hypothetical tidying: batching the classroom-config scaffolds into one
        # commit moved them off Contents, and the first cohort org bootstrapped afterwards
        # could not seed that repo at all - the roster, schedule and people.yml never
        # landed, and every later step that reads them failed in turn.
        return _seed_first_commit(org, repo, tree, message)
    payload: dict[str, Any] = {"tree": tree, "base_tree": parent[1]}
    code, new_tree = gh(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{repo}/git/trees",
        "--input",
        "-",
        "--jq",
        ".sha",
        stdin=json.dumps(payload),
    )
    if code != 0:
        log_err(f"could not build a tree for {org}/{repo}: {new_tree[:200]}")
        return False
    code, commit = gh(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{repo}/git/commits",
        "--input",
        "-",
        "--jq",
        ".sha",
        stdin=json.dumps(
            {
                "message": message,
                "tree": new_tree.strip(),
                "parents": [parent[0]] if parent else [],
            }
        ),
    )
    if code != 0:
        log_err(f"could not commit to {org}/{repo}: {commit[:200]}")
        return False
    # An existing branch is MOVED; a repo whose first commit this is has no ref to move,
    # so the ref is created instead.
    if parent:
        code, out = gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{org}/{repo}/git/refs/heads/{branch}",
            "--raw-field",
            f"sha={commit.strip()}",
        )
    else:
        code, out = gh(
            "api",
            "--method",
            "POST",
            f"repos/{org}/{repo}/git/refs",
            "--raw-field",
            f"ref=refs/heads/{branch}",
            "--raw-field",
            f"sha={commit.strip()}",
        )
    if code != 0:
        log_err(f"could not move {org}/{repo}@{branch}: {out[:200]}")
        return False
    return True


def seed_if_absent(
    org: str, repo: str, path: str, content: bytes, message: str
) -> bool:
    """Write a file create-only, never an overwrite - the single home of the "USER-owned
    file, leave it exactly as faculty left it" rule.

    Returns True whenever the file is now present as intended - whether it was WRITTEN just
    now, OR was already present and left untouched (logged as a skip, so a re-run's output
    shows what was left alone). Returns False ONLY when a write was attempted and FAILED, so
    a caller can `if not seed_if_absent(...): failures += 1` to count real failures without a
    skip of a live file counting as one. Callers keep their own comment on WHY a given file
    is create-only; this owns HOW."""
    if get_file_content(org, repo, path) is not None:
        log_skip(f"{repo}/{path}")
        return True
    return put_file(org, repo, path, content, message)


# The mark that says a seeded stub is STILL the scaffold's - present in every stub we write,
# and the one thing that makes it safe to improve. A stub is refreshed while it carries the
# mark and never touched again once faculty remove it, which they do by writing their own
# (each stub tells them so). Without this a stub was create-only forever: the courses
# already running kept the first version we ever shipped, and every later improvement
# reached new repos only.
#
# The legacy strings are the stubs we shipped before the mark existed, so the repos that
# have those pick the new versions up too. Nothing is ever added here that a faculty member
# might plausibly have typed themselves.
STUB_MARK = "dsl-stub:"
# Every wording we have ever seeded, so a repo carrying an older one is still recognised.
# Nothing goes in here that a faculty member might plausibly have typed themselves.
STUB_MARKS = (
    STUB_MARK,
    "Replace with the real syllabus.",
    "This file is the reading list students see.",
    "This file IS the reading list students see on the site's Readings tab",
)


def is_untouched_stub(text: str) -> bool:
    """Whether `text` is still a stub this toolkit seeded, rather than faculty writing."""
    return any(m in text for m in STUB_MARKS)


def get_file_content(org: str, repo: str, path: str, ref: str = "") -> str | None:
    """Fetch a file's decoded text content (from `ref`, default branch if empty).

    None means the file is genuinely absent (a 404) - nothing else. Any other failure to
    read it (no permission, rate limit, network) raises, because callers treat None as
    "not configured yet" and would otherwise read a transient API failure as an empty
    roster/schedule/registry and cheerfully do nothing. Same rule as repo_blob_shas."""
    url = f"repos/{org}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    code, out = gh(
        "api",
        url,
        "--jq",
        ".content | @base64d",
    )
    if code != 0:
        if is_missing_resource(out):
            return None
        raise RuntimeError(f"could not read {org}/{repo}/{path}: {out[:200]}")
    return out


def get_file_with_sha(
    org: str, repo: str, path: str, ref: str = ""
) -> tuple[str, str] | None:
    """`(decoded text, blob sha)` for a file, or None if it is genuinely absent (a 404).

    The read half of a safe read-modify-write: hand the sha back to
    `put_file(..., expected_sha=...)` and the write is refused if anything else committed
    to the file in between. Same fail-loud rule as get_file_content - any failure that is
    NOT a 404 raises, because a caller treating it as "not there" would write over a file
    it never managed to read.

    The sha comes first in the jq output, on its own line, because the content may contain
    newlines and a sha may not."""
    url = f"repos/{org}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    code, out = gh("api", url, "--jq", r'"\(.sha)\n" + (.content | @base64d)')
    if code != 0:
        if is_missing_resource(out):
            return None
        raise RuntimeError(f"could not read {org}/{repo}/{path}: {out[:200]}")
    sha, _, text = out.partition("\n")
    return text, sha


def repo_tree(org: str, repo: str, branch: str, kind: str = "") -> tuple[str, ...]:
    """Every path of type `kind` in `org/repo`'s `branch`, sorted - ONE recursive git-tree
    fetch, shared by both transports that need a repo's structure: `kind="tree"` is the
    directories (dsl_course.discovery's session-folder discovery), `kind="blob"` the files
    (site._repo_tree's material links). The two used to fetch the same tree with their own
    error handling, and only one of them was fail-loud. `kind=""` is every path of either
    kind, for a caller that just asks "is this path in the repo" and does not care whether
    the answer is a file or a folder - one fetch rather than two of the same tree.

    An absent or empty tree is `()` - see `_tree`, which also owns the fail-loud rule that
    keeps an unreadable tree from republishing a cohort site with every material link and
    every session row deleted, silently and green.
    """
    select = f' | select(.type=="{kind}")' if kind else ""
    return tuple(
        sorted(_tree(org, repo, branch, f'"\\(.truncated)", (.tree[]{select} | .path)'))
    )


def load_yaml_config(org: str, repo: str, path: str) -> dict | None:
    """Fetch + parse a YAML config file into a mapping, correctly distinguishing the three
    states callers that prune depend on:

    - ABSENT -> None (get_file_content returned None on a genuine 404). Do not prune.
    - present but empty -> {} (the file exists but parses to nothing). A legitimate
      "empty the team" for pruning callers.
    - present with content -> the parsed mapping.

    Any OTHER read failure propagates (get_file_content raises on non-404 - preserved
    here). Malformed YAML, or a non-mapping top level (a list/scalar), is logged (naming
    org/repo/path) and raised - never silently coerced to {}, which is exactly the
    "or '' erases None-vs-content" class of bug this replaces."""
    content = get_file_content(org, repo, path)
    if content is None:
        return None
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        log_err(f"malformed YAML in {org}/{repo}/{path}: {exc}")
        raise
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = (
            f"{org}/{repo}/{path} is not a YAML mapping "
            f"(got {type(data).__name__}) - refusing to use it"
        )
        log_err(msg)
        # RuntimeError (not TypeError) to match the house style for a bad read/config -
        # get_file_content and list_org_repos raise it too, and status.main catches it.
        raise RuntimeError(msg)  # noqa: TRY004
    return data
