"""When a path changed: the commit-history readers that answer with a moment.

Beside `gh_contents`'s commit readers that answer with WHO (`last_committer`,
`path_committers`); this one answers WHEN.
"""

from __future__ import annotations

from datetime import datetime

from .ghcli import gh


def last_commit_at(org: str, repo: str, path: str = "") -> datetime | None:
    """When `path` (or the repo, for "") last changed on the default branch. None when
    there is no such commit or it could not be read - staleness is a hint, not a gate."""
    query = f"repos/{org}/{repo}/commits?per_page=1" + (f"&path={path}" if path else "")
    code, out = gh("api", query, "--jq", ".[0].commit.committer.date // empty")
    if code != 0 or not out.strip():
        return None
    try:
        return datetime.fromisoformat(out.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
