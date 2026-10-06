"""When a path changed: the commit-history readers that answer with a moment.

Beside `gh_contents`'s commit readers that answer with WHO (`last_committer`,
`path_committers`); these answer WHEN.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

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


def first_landed(org: str, repo: str, folder: str) -> datetime | None:
    """When the first commit touching `folder` landed in `org/repo` (its oldest commit
    date), or None when no commit touches it. Raises when GitHub cannot say."""
    code, out = gh(
        "api",
        "--paginate",
        f"repos/{org}/{repo}/commits?path={quote(folder)}&per_page=100",
        "--jq",
        ".[-1].commit.committer.date",
    )
    if code != 0:
        raise RuntimeError(f"could not read the history of {org}/{repo}: {out[:200]}")
    dates = [line.strip() for line in out.splitlines() if line.strip()]
    return datetime.fromisoformat(dates[-1]) if dates else None
