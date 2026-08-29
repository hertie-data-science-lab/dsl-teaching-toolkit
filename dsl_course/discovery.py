"""Discover, over the GitHub API, what a course/cohort org actually contains.

Everything the workflow renderers need to populate their dropdowns (cohort orgs, target
repos, assignment templates, sections/sessions), and everything the site generator needs
to find released content - read live from the orgs themselves, so there is no declared
config to drift out of date.

The cohort registry is the one exception to "read it from the live org": cohort orgs
can't be found by naming convention (they're arbitrary), so they're listed explicitly in
the course org's .github/cohort-courses-pages.yml (register_cohort appends; faculty &
instructors can edit it by hand).

The session-folder rule itself lives in course.session_dirs - this module is only the API
transport for it (a recursive git-tree fetch); course.discover_sections is the local
filesystem transport of the same rule.
"""

from __future__ import annotations

import json

import yaml

from .central import resolve_central_ref
from .course import (
    COHORT_TOPIC,
    COURSE_CONFIG,
    COURSE_HUB_TOPIC,
    GRADEBOOK_PREFIX,
    session_dirs,
)
from .gh_contents import get_file_content, load_yaml_config, put_file, repo_tree
from .ghcli import gh
from .log import log_err, log_ok
from .repos import default_branch

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
# The repos only a cohort org has - the fallback tier signal for an org bootstrapped
# before the topics existed, or whose topic stamp never landed.
COHORT_ONLY_REPOS = {"welcome", "classroom-config"}


def org_tier(repos: list[dict]) -> str | None:
    """`"cohort"`, `"course"`, or None when the listing cannot say.

    The `.github` repo's topic is authoritative; the cohort-only infra repos are the
    fallback. None is a real answer, not "course": a legacy cohort (`hertie-dl-f2025`:
    `.github` + student repos, no `welcome`, no topics) looks exactly like a course org by
    elimination, and the faculty-access sweep treats "course" as "push everywhere"."""
    dotgithub = next((r for r in repos if r["name"] == ".github"), None)
    topics = set((dotgithub or {}).get("topics") or [])
    if COHORT_TOPIC in topics:
        return "cohort"
    if COURSE_HUB_TOPIC in topics:
        return "course"
    if any(r["name"] in COHORT_ONLY_REPOS for r in repos):
        return "cohort"
    return None


def classify_repos(repos: list[dict]) -> dict[str, str | None]:
    """`{repo name: the cohort assignment template it derives from, or None}`.

    THE submission-repo rule, computed ONCE for a whole listing. A submission repo is
    generated from one of the org's cohort assignment templates, so its name is that
    template's name plus a `-<handle>` or `-<team>` suffix.

    Longest template first: `assignment-4` and `assignment-4-project` both prefix
    `assignment-4-project-ada-l`, and only the longer one leaves a suffix that is a handle
    rather than `project-ada-l`. Templates themselves map to None - `assignment-4-project`
    is a repo in this listing AND starts with `assignment-4-`, so a cohort holding both
    would otherwise read one of its own templates as a submission belonging to `project`.
    """
    templates = sorted(
        (r["name"] for r in repos if r.get("isTemplate")), key=len, reverse=True
    )
    return {
        r["name"]: None
        if r.get("isTemplate")
        else next((t for t in templates if r["name"].startswith(f"{t}-")), None)
        for r in repos
    }


def is_student_repo(repo: dict, derived: dict[str, str | None]) -> bool:
    """Whether `repo` is a per-student/team repo, by topic OR by name.

    `derived` is one `classify_repos` over the same listing. The topic that marks a
    submission repo is stamped after the create and never converged, so a repo merely
    NAMED off a template counts too: on a public page, and in the faculty-access floor,
    the roster must not depend on one PATCH having landed."""
    return _has_infra_topic(repo) or derived.get(repo["name"]) is not None


def student_repo_names(repos: list[dict]) -> frozenset[str]:
    """The per-student and per-team repos in a listing - submission repos and gradebooks."""
    derived = classify_repos(repos)
    return frozenset(r["name"] for r in repos if is_student_repo(r, derived))


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
    return _has_infra_topic(repo)


def _has_infra_topic(repo: dict) -> bool:
    """Whether `repo`'s TOPICS mark it machinery - a submission repo, a frozen cohort
    assignment template, or a private gradebook. A gradebook is recognised by NAME too:
    the topic is stamped in a separate call after the create, and a failed stamp must not
    put `grades-<handle>` on a public page."""
    if repo["name"].startswith(GRADEBOOK_PREFIX):
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
        "isTemplate: .is_template, archived, topics: (.topics // [])}",
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
    and a bare top-level list has always been accepted too. Anything else - YAML that does
    not parse, a scalar, or a cohort list that isn't all strings - is malformed, logged and
    raised, never silently flattened to [] (which downstream renders every dropdown as
    "(none-yet)" and lets a whole-course sync go quietly green)."""
    content = get_file_content(course_org, ".github", COHORTS_PATH)
    if not content:
        return []
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        # Unparseable is malformed, exactly like the shape check below - and the bare
        # safe_load surfaced it as a raw PyYAML traceback from wherever the registry
        # happened to be read, naming a "<unicode string>" rather than the file.
        msg = f"malformed cohort registry in {course_org}/.github/{COHORTS_PATH}: {exc}"
        log_err(msg)
        raise RuntimeError(msg) from exc
    cohorts = data.get("cohorts", []) if isinstance(data, dict) else data
    if not isinstance(cohorts, list) or not all(isinstance(c, str) for c in cohorts):
        msg = (
            f"malformed cohort registry in {course_org}/.github/{COHORTS_PATH}: "
            f"expected a list of cohort org names (bare, or under a 'cohorts:' key)"
        )
        log_err(msg)
        raise RuntimeError(msg)
    return [c for c in cohorts if c]


def org_meta(org: str) -> dict:
    """An org's `.github/dsl-course.yml`, or `{}` when it declares none.

    THE read for an org's declared identity - the course name, the faculty SSOT, a
    cohort's `course:` pointer, the `central_ref:` its workflows run. `{}` for a genuine
    404 or an empty file; a MALFORMED one still raises, because reading a typo as "this
    org declares nothing" files a cohort under the course orgs and rewrites the inventory
    around it. The one caller that must tell ABSENT from EMPTY - sync_faculty, which
    would otherwise prune every admin - reads load_yaml_config directly."""
    return load_yaml_config(org, ".github", COURSE_CONFIG) or {}


def course_name_for_cohort(cohort_org: str) -> str:
    """This cohort's course name, for student-facing prose ("your grades for X").

    Follows the cohort's own `.github/dsl-course.yml` `course:` pointer to its course
    org, then reads that org's identity file - the same two hops status.collect makes,
    but starting from the cohort, which is all an emailer is given.

    Returns "" when either file is missing or carries no name, so callers fall back to
    generic wording. A student must never be emailed a blank or a literal placeholder
    where the course name belongs.
    """
    pointer = org_meta(cohort_org)
    return course_name_of(str(pointer.get("course") or ""))


def course_name_of(course_org: str) -> str:
    """A COURSE org's display name from its own identity file. "" when unnamed or absent.

    The toolkit's single spelling of that fallback - `course_name`, else `org_name` - so a
    cohort landing page, a status row and an email cannot disagree about what a course is
    called. Takes "" and returns "" so a caller holding a cohort pointer that names no
    course org needs no guard of its own."""
    if not course_org:
        return ""
    meta = org_meta(course_org)
    return str(meta.get("course_name") or meta.get("org_name") or "")


def central_ref_for(org: str) -> str:
    """Which ref of the central toolkit this org's seeded workflows run the engine from.

    Declared as `central_ref:` in the COURSE org's `.github/dsl-course.yml`, so one edit
    moves a course and every cohort under it between tiers together. A cohort org's own
    file is only a pointer (`course:`), so this follows it - a `central_ref:` written into
    a cohort's file is ignored, because a cohort running a different engine from the course
    org that releases into it is not a state anyone wants to debug.

    Absent, or unreadable as a tier, means `central.CENTRAL_REF` - see resolve_central_ref
    for why junk falls back rather than failing the run."""
    meta = org_meta(org)
    course = str(meta.get("course") or "")
    if course:
        org, meta = course, org_meta(course)
    return resolve_central_ref(
        meta.get("central_ref"), source=f"{org}/.github/{COURSE_CONFIG}"
    )


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
    """Drop cohort_org from the course's registry (idempotent), and NOT the mirror image
    of `register_cohort`: the registry is APPEND-ON-INTENT, PRUNE-ON-REALITY.

    Adding stays a deliberate act, because a cohort's absence can be intended - a faculty
    member may unregister one to stop its nightly syncs. Removal cannot: a cohort dropped
    from here is invisible to every nightly sync, which is a SILENT no-op, where a stale
    entry merely fails loudly once a night. So the liveness verdict belongs to the caller
    (`seed._live_cohorts`) and this only writes down what it was told.

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

    The fetch itself (and its absent-vs-failed discrimination) is gh_contents.repo_tree, shared
    with the site builder's blob-side twin: an absent/empty tree is genuinely no
    directories, any other failure RAISES. It must never come back as "no sessions" - the
    site clears and rewrites its collections from these rows, so one rate-limited fetch
    would republish the cohort site with every session row deleted."""
    return repo_tree(org, repo, default_branch(org, repo, fallback="main"), "tree")


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
    hand-out happened, whatever route fired it - the scheduled pin, the manual workflow, or a
    `releases:` entry's `assignment:`.

    The site gates an assignment's brief on this (see `site._assignment_entry`), which is
    why it reads what SHIPPED rather than what the plan intended: a hand-out with no
    `handout_datetime` pinned - the manual workflow's documented mode - is invisible to the
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
    """Repos that should HOST the release workflows: the materials repo(s), not the infra
    repos (_is_infra_repo - notably NOT the public `<org>.github.io` site repo, which
    would otherwise be handed the org-admin token as a repo secret) and not the
    assignment-* template repos (those are generate sources - equipping them would copy
    the faculty & instructors workflows into every student repo)."""
    return sorted(
        r["name"]
        for r in list_org_repos(course_org)
        if not _is_infra_repo(r) and not r["name"].startswith("assignment-")
    )
