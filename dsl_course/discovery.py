"""Discover, over the GitHub API, what a course/cohort org actually contains.

Everything the workflow renderers need to populate their dropdowns (cohort orgs, target
repos, assignment templates, sections/sessions), and everything the site generator needs
to find released content - read live from the orgs themselves, so there is no declared
config to drift out of date.

The cohort registry is the one exception to "read it from the live org": cohort orgs
can't be found by naming convention (they're arbitrary), so they're listed explicitly in
the course org's .github/cohort-courses-pages.yml (register_cohort appends; faculty &
instructors can edit it by hand).

The session-folder rule itself lives in utils.session_dirs - this module is only the API
transport for it (a recursive git-tree fetch); utils.discover_sections is the local
filesystem transport of the same rule.
"""

from __future__ import annotations

import json

import yaml

from .utils import (
    get_default_branch,
    get_file_content,
    gh,
    log_err,
    log_ok,
    put_file,
    repo_tree,
    session_dirs,
)

COHORTS_PATH = (
    "cohort-courses-pages.yml"  # standalone registry in the course org's .github repo
)

INFRA_REPOS = {"welcome", "classroom-config", ".github"}
# The topic assign.py stamps on the frozen cohort-side template it creates before
# provisioning a single student repo (ensure_cohort_template). Named here, and imported by
# the one writer and the one reader, so the string cannot drift between them.
ASSIGNMENT_TEMPLATE_TOPIC = "assignment-template"
# Topics marking a repo as machinery rather than faculty-authored content: per-student
# submission repos and the frozen cohort-side assignment templates (assign.py), and the
# private per-student gradebooks (grades.py).
INFRA_TOPICS = {"submission", ASSIGNMENT_TEMPLATE_TOPIC, "gradebook"}


def _is_infra_repo(repo: dict) -> bool:
    """Whether `repo` (a list_org_repos entry) is machinery rather than course content.

    The single exclusion list behind BOTH discovery functions, so a repo type added on
    one side can't leak into the other: a generated `<org>.github.io` site repo (public!)
    must never be treated as a content repo - discover_content_repos' repos HOST the
    faculty workflows and get the org-admin DSL_BOT_TOKEN set as a repo secret - and a
    private `grades-<handle>` gradebook must never show up as a release target or get
    tree-scanned for sessions.
    """
    name = repo["name"]
    if name in INFRA_REPOS or name.endswith(".github.io"):
        return True
    return bool(set(repo.get("topics") or []) & INFRA_TOPICS)


def list_org_repos(org: str) -> list[dict]:
    """Every repo in `org`, fully paginated - the one repo listing every discovery
    helper here goes through.

    `gh repo list` needs a fixed `--limit`, and a cohort org holds a repo per student
    per assignment plus a gradebook each, so any fixed cap silently truncates discovery
    (missing release targets, un-refreshed workflows). `gh api --paginate` walks every
    page instead. `--jq` emits one JSON object per line per page, so the pages are
    parsed as NDJSON rather than concatenated arrays.

    Fields are normalised to the names the callers use (`url`, `isTemplate`).

    An empty list means the org genuinely holds no repos; a failed listing raises, since
    every caller reads "no repos" as "nothing to do" (refresh converges zero repos and
    reports success, profile_readme misfiles a cohort org as a course org).
    """
    code, out = gh(
        "api",
        "--paginate",
        f"orgs/{org}/repos?per_page=100",
        "--jq",
        ".[] | {name, description, visibility, url: .html_url, "
        "isTemplate: .is_template, topics: (.topics // [])}",
    )
    if code != 0:
        raise RuntimeError(f"could not list repos in {org}: {out[:200]}")
    try:
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"unparseable repo listing for {org}: {out[:200]}") from exc


def _read_cohorts(course_org: str) -> list[str]:
    """Read the course org's standalone .github/cohort-courses-pages.yml registry.

    A genuinely absent or empty registry is [] (a valid brand-new course org). The
    machine-written form is a `{cohorts: [...]}` mapping, but the file is human-editable
    and a bare top-level list has always been accepted too. Anything else - a scalar, or
    a cohort list that isn't all strings - is malformed, logged and raised, never silently
    flattened to [] (which downstream renders every dropdown as "(none-yet)" and lets a
    whole-course sync go quietly green)."""
    content = get_file_content(course_org, ".github", COHORTS_PATH)
    if not content:
        return []
    data = yaml.safe_load(content)
    cohorts = data.get("cohorts", []) if isinstance(data, dict) else data
    if not isinstance(cohorts, list) or not all(isinstance(c, str) for c in cohorts):
        msg = (
            f"malformed cohort registry in {course_org}/.github/{COHORTS_PATH}: "
            f"expected a list of cohort org names (bare, or under a 'cohorts:' key)"
        )
        log_err(msg)
        raise RuntimeError(msg)
    return [c for c in cohorts if c]


def discover_cohorts(course_org: str) -> list[str]:
    """Cohort orgs are listed explicitly in the course's .github/cohort-courses-pages.yml
    (naming-independent). `bootstrap --cohort --course X` appends; faculty & instructors can edit it."""
    return sorted(_read_cohorts(course_org))


def register_cohort(course_org: str, cohort_org: str) -> bool:
    """Append cohort_org to the course's cohort-courses-pages.yml registry (idempotent).

    Returns True if the cohort is registered afterwards (already present, or the write
    succeeded), False if the write failed - so bootstrap doesn't claim a cohort was
    registered when the put_file actually failed."""
    cohorts = set(_read_cohorts(course_org))
    if cohort_org in cohorts:
        log_ok(f"{cohort_org} already in {course_org}/.github/{COHORTS_PATH}")
        return True
    return _write_cohorts(
        course_org,
        cohorts | {cohort_org},
        f"registry: add cohort {cohort_org}",
        failure=(
            f"failed to register {cohort_org} under {course_org}: the registry write "
            f"to {COHORTS_PATH} failed"
        ),
        success=f"registered {cohort_org} under {course_org}",
    )


def _write_cohorts(
    course_org: str, cohorts: set[str], commit: str, *, failure: str, success: str
) -> bool:
    """Serialise the registry and write it back, reporting either way. The one place the
    file's SHAPE is decided, so the two callers that edit it cannot disagree about it -
    and the one place a write failure is turned into a False, so neither can claim an edit
    that did not land."""
    body = yaml.safe_dump({"cohorts": sorted(cohorts)}, sort_keys=False)
    if not put_file(course_org, ".github", COHORTS_PATH, body.encode(), commit):
        log_err(failure)
        return False
    log_ok(success)
    return True


def unregister_cohort(course_org: str, cohort_org: str) -> bool:
    """Drop cohort_org from the course's registry (idempotent). The prune half of
    `register_cohort`, and deliberately NOT its mirror image.

    The registry is APPEND-ON-INTENT, PRUNE-ON-REALITY. Adding stays a deliberate act
    (Bootstrap cohort), because a cohort's absence can be intended - a faculty member may
    unregister one to stop its nightly syncs, and a refresh that re-added every org it
    discovered would silently override that. Removal cannot be intent in the same way:
    the caller has already established that the ORG ITSELF is gone (see `seed.refresh`),
    and nothing can be synced into an org that does not exist.

    Removing on anything weaker than that would be the worse bug. A cohort dropped from
    here is invisible to every nightly sync - membership, faculty, site, scheduler - which
    is a SILENT no-op, where a stale entry merely fails loudly once a night. So the
    liveness verdict belongs to the caller (`utils.org_exists`, which raises rather than
    guessing), and this function only writes down what it was told.

    Returns True if the cohort is absent from the registry afterwards."""
    cohorts = set(_read_cohorts(course_org))
    if cohort_org not in cohorts:
        return True
    return _write_cohorts(
        course_org,
        cohorts - {cohort_org},
        f"registry: drop deleted cohort {cohort_org}",
        failure=(
            f"failed to unregister the deleted org {cohort_org} from {course_org}: the "
            f"registry write to {COHORTS_PATH} failed - every sync will keep trying it"
        ),
        success=f"unregistered {cohort_org} from {course_org} (the org no longer exists)",
    )


def discover_cohort_repos(cohort_orgs: list[str]) -> list[str]:
    """Candidate target repos: real cohort content repos, excluding everything
    _is_infra_repo covers (infra, the website, submission repos, assignment templates,
    gradebooks). Only what genuinely exists - no placeholder default, so an org with
    nothing registered yet correctly shows an empty (not phantom) dropdown."""
    repos: set[str] = set()
    for org in cohort_orgs:
        repos |= {r["name"] for r in list_org_repos(org) if not _is_infra_repo(r)}
    return sorted(repos)


def _repo_tree_dirs(org: str, repo: str) -> tuple[str, ...]:
    """Every directory path in a repo's default-branch tree - one recursive fetch,
    shared by every discovery helper that needs a repo's directory structure (rather
    than listing each top-level directory individually - N+1 API calls).

    The fetch itself (and its absent-vs-failed discrimination) is utils.repo_tree, shared
    with the site builder's blob-side twin: an absent/empty tree is genuinely no
    directories, any other failure RAISES. It must never come back as "no sessions" - the
    site clears and rewrites its collections from these rows, so one rate-limited fetch
    would republish the cohort site with every session row deleted."""
    return repo_tree(org, repo, get_default_branch(org, repo), "tree")


def _section_session_pairs(org: str, repo: str) -> list[tuple[str, int]]:
    """(section, session_number) for every immediate child - across every top-level
    directory - whose name has an ordinal prefix."""
    return [
        (section, n)
        for section, _, n in session_dirs(_repo_tree_dirs(org, repo))
        if section
    ]


def discover_sessions(org: str, repo: str) -> list[str]:
    """Session numbers present in a content repo, across every discovered section.
    Used by the public-site builder to walk a source repo session by session."""
    return [str(n) for n in sorted({n for _, n in _section_session_pairs(org, repo)})]


def discover_release_sources(
    org: str, content_repos: list[str]
) -> list[tuple[str, str, str, int]]:
    """(repo, subpath, folder_name, session_number) for every session folder found
    across a cohort's `content_repos` (see discover_cohort_repos), covering both shapes
    a release can produce: nested - a session folder inside a subpath of a shared repo,
    `subpath/NN_.../` - or root - `NN_.../` directly at the repo root (what a `deploy:`
    with a bare `cohort_dest_repo` and no `cohort_dest_path` produces). One recursive tree fetch per
    repo; the exact folder name is captured too, so callers can list its files directly
    with no further discovery call."""
    return [
        (repo, subpath, folder, n)
        for repo in content_repos
        for subpath, folder, n in session_dirs(_repo_tree_dirs(org, repo))
    ]


def discover_assignments(course_org: str) -> list[str]:
    """Assignment template repos in the course org (named assignment-*) - the dropdown."""
    return sorted(
        r["name"]
        for r in list_org_repos(course_org)
        if r["name"].startswith("assignment-") and r.get("isTemplate")
    )


def discover_handed_out_assignments(cohort_org: str) -> frozenset[str]:
    """The cohort-side name of every assignment this cohort has ACTUALLY been given.

    assign.py's stage 1 freezes a cohort-level template repo named exactly the cohort-side
    name (`schedule.cohort_name` - the slug unless `cohort_dest_repo` renames it) and
    topics it `assignment-template`, before it provisions a single student repo
    (`ensure_cohort_template`). So that repo existing IS the cohort-side record that the
    hand-out happened, whatever route fired it - the scheduled pin, the manual button, or a
    `releases:` entry's `assignment:`.

    The site gates an assignment's brief on this (see `site._assignment_entry`), which is
    why it reads what SHIPPED rather than what the plan intended: a hand-out with no
    `handout_datetime` pinned - the manual button's documented mode - is invisible to the
    plan, and gating on the plan alone published those briefs on sight.

    This is a second listing of an org `sync_site` already listed, and it must stay one:
    memoising `list_org_repos` would serve assign.py a listing taken BEFORE it created the
    template repo it then syncs the site for, withholding the brief it just handed out."""
    return frozenset(
        r["name"]
        for r in list_org_repos(cohort_org)
        if ASSIGNMENT_TEMPLATE_TOPIC in (r.get("topics") or [])
    )


def discover_content_repos(course_org: str) -> list[str]:
    """Repos that should HOST the release buttons: the materials repo(s), not the infra
    repos (_is_infra_repo - notably NOT the public `<org>.github.io` site repo, which
    would otherwise be handed the org-admin token as a repo secret) and not the
    assignment-* template repos (those are generate sources - equipping them would copy
    the faculty & instructors workflows into every student repo)."""
    return sorted(
        r["name"]
        for r in list_org_repos(course_org)
        if not _is_infra_repo(r) and not r["name"].startswith("assignment-")
    )
