"""The course DOMAIN vocabulary - the names and rules that describe a course, with no
GitHub or I/O of its own (stdlib only).

Everything here was previously spelled out two or three times across the modules that
needed it. Declared once, in the layer everything else can import, so a rename or a
reworded rule cannot reach one consumer and miss another.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

# The per-org identity/config file, at the root of every org's `.github` repo: a course
# org's declares its name and its faculty SSOT, a cohort org's is a pointer back to it.
COURSE_CONFIG = "dsl-course.yml"
# The private per-cohort config repo: roster, teams, schedule, grades, autograde records.
# Every cohort org has exactly one, under exactly this name.
CONFIG_REPO = "classroom-config"
# The per-student gradebook repo: grades-<handle> (grades.py creates them, discovery reads
# them back). Named here so the reader and the writer cannot drift.
GRADEBOOK_PREFIX = "grades-"
# Topics on an org's `.github` repo that say which TIER the org is (bootstrap_course stamps
# them; list_orgs enumerates orgs by them). The repo listing carries them, so a sweep can
# tell a course org from a cohort without another read.
COURSE_HUB_TOPIC = "dsl-course-hub"
COHORT_TOPIC = "dsl-cohort"
# How `scaffold_materials` names every materials repo (`course-materials-<tag>`) - the New
# materials repo workflow takes only the tag, so this prefix is guaranteed by the toolkit
# rather than a convention faculty could deviate from. Named here because `seed.refresh`
# has to recognise a materials repo among the code and dataset repos that
# `discover_content_repos` returns alongside it, and a rename reaching only one side would
# silently stop the convergence it gates.
MATERIALS_REPO_PREFIX = "course-materials-"
# Generated faculty-side files, named where every module that has to know about them can
# see it: `scaffold` writes them, `deploy` refuses to release them, `syllabus` builds one.
# Named rather than re-spelled per module, so the exclusion cannot lapse when one is renamed.
SYLLABUS_SAMPLE_FILE = "SYLLABUS.md.sample"
SYLLABUS_SESSIONS_FILE = "SYLLABUS.sessions.md"
# The faculty-only heading in the materials README that `scaffold` seeds. `deploy` refuses
# to release a README still containing it, so the sentinel is declared ONCE here - the
# writer and the guard both import it, and neither can lapse when the wording is edited.
FACULTY_ONLY_HEADING = "delete this section before releasing the README"
# The branch an assignment template keeps its solution and grading.yml on.
SOLUTION_BRANCH = "solution"

# The four ROLE teams every org's access is expressed in: the two faculty teams, created
# in course and cohort orgs alike, and the two cohort-only student teams. Named here
# because the grants (access), the reconciles (sync_faculty, sync_roster), the bootstrap
# that creates them and the slugs the student-written Join-team form may never claim
# (sync_teams) all address them by these exact strings.
INSTRUCTORS_TEAM = "instructors"
COURSE_ADMIN_TEAM = "course-admin"
STUDENTS_TEAM = "students"
AUDITORS_TEAM = "auditors"
ROLE_TEAMS = frozenset(
    {INSTRUCTORS_TEAM, COURSE_ADMIN_TEAM, STUDENTS_TEAM, AUDITORS_TEAM}
)


def submission_repo(slug: str, suffix: str) -> str:
    """A submission repo's name: `<slug>-<handle>` individually, `<slug>-<team>` for a
    group. One composition, so the provisioner, the grader and the "your repo is called"
    line on the site cannot spell it differently."""
    return f"{slug}-{suffix}"


def submission_suffix(repo: str, template: str) -> str:
    """The handle-or-team half of `submission_repo` - what is left once the template the
    repo was generated from is taken off the front."""
    return repo[len(template) + 1 :]


def term_tag(name: str) -> str | None:
    """The fYYYY / sYYYY term tag in an org or repo name (`course-materials-F2026` ->
    'f2026'), or None. Case-insensitive and lowercased, so the same name cannot yield a tag
    on one code path and nothing on another - which two of the three copies of this regex
    did before they were folded into it."""
    m = re.search(r"[fs]\d{4}", name.lower())
    return m.group(0) if m else None


def pages_repo(org: str) -> str:
    """The GitHub Pages org site repo for an org - pushing it redeploys the site.

    Named `<org>.github.io` so it serves at the org root; `scaffold` creates it under this
    name and `site` syncs it, so the two cannot spell it differently."""
    return f"{org.lower()}.github.io"


def assignment_slug(template: str) -> str:
    """assignment-1-f2026 -> assignment-1 (drop a trailing cohort suffix)."""
    return re.sub(r"-[fs]\d{4}$", "", template)


def resolve_is_group(
    *, force: bool, schedule_type: str | None, template_group: bool | None
) -> bool:
    """The SINGLE precedence for group-vs-individual, shared by every resolver.

    An explicit force (the Grade-assignment workflow's checkbox / `--group`) wins; else the
    COHORT's declaration - `assignments.<slug>.type` in classroom-config/schedule.yml, passed
    as `schedule_type`; else the template's design-time grading.yml `type:`, passed as
    `template_group` (True/False, or None when not consulted); else individual. Pure: each
    caller passes the inputs it already holds, so no consumer re-derives its own precedence
    (and none re-trusts student-writable teams.csv to decide the kind)."""
    if force:
        return True
    if schedule_type is not None:
        return schedule_type == "group"
    if template_group is not None:
        return template_group
    return False


def coerce_date(value: object) -> date | None:
    """A YAML date/datetime or an ISO `YYYY-MM-DD` string -> a `date` (None if unparseable).
    Date-level only (whole-day). The single canonical date coercion: `active_today` here and
    `schedule._coerce_date` both use it, so the two can never drift. An unquoted
    `start: 2026-09-01` in YAML parses to a `datetime.date` (or `datetime`), not a string;
    a quoted one is a string - both land on the same `date`."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):  # date and its datetime subclass both land here
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def active_today(start: str | date | None, end: str | date | None, today: str) -> bool:
    """Whether `today` (ISO date string) falls within [start, end], either bound optional
    (open-ended if omitted). Bounds may be ISO strings or `datetime.date` objects (an
    unquoted YAML date); an unparseable bound is treated as absent (open-ended on that side)."""
    today_d = coerce_date(today)
    start_d = coerce_date(start)
    end_d = coerce_date(end)
    if start_d and today_d and today_d < start_d:
        return False
    if end_d and today_d and today_d > end_d:  # noqa: SIM103 - guards mirror the docstring
        return False
    return True


# Session directories are named "<ordinal>_<free text>" (e.g. "00_intro",
# "07_finals-review") - only the leading, zero-padding-tolerant ordinal is meaningful;
# the rest is whatever the course calls it. No "week"/"session" literal is required.
_SESSION_PREFIX_RE = re.compile(r"^0*(\d+)_")


def session_number(name: str) -> int | None:
    """Extract the ordinal prefix from a directory name ('00_intro' -> 0, '07_x' -> 7),
    or None if it doesn't start with digits followed by an underscore."""
    m = _SESSION_PREFIX_RE.match(name)
    return int(m.group(1)) if m else None


def session_dirs(dir_paths: Iterable[str]) -> list[tuple[str, str, int]]:
    """THE session-folder rule, over a flat list of relative directory paths.

    `(parent, folder_name, session_number)` for every ordinal-prefixed directory found
    at depth 1 (`NN_.../` - the repo itself is one section, so parent is "") or depth 2
    (`section/NN_.../` - a named section). Anything deeper, and anything without an
    ordinal prefix, is not a session folder. A `parent` is therefore exactly a
    releasable section.

    One rule, two transports: the local filesystem (discover_sections here, used by
    the public-site builder) and the GitHub trees API (dsl_course.discovery) both feed their
    directory listing through this, so "ordinal-prefixed directory = session folder"
    is defined once.
    """
    found = []
    for path in dir_paths:
        parts = path.split("/")
        if len(parts) > 2:
            continue
        n = session_number(parts[-1])
        if n is None:
            continue
        found.append((parts[0] if len(parts) == 2 else "", parts[-1], n))
    return found


def _local_dir_paths(repo_root: Path) -> list[str]:
    """The relative paths of every directory in `repo_root` down to depth 2 - the
    filesystem transport for session_dirs (the API side fetches a git tree instead)."""
    if not repo_root.is_dir():
        return []
    paths = []
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir():
            continue
        paths.append(child.name)
        paths += [
            f"{child.name}/{grandchild.name}"
            for grandchild in sorted(child.iterdir())
            if grandchild.is_dir()
        ]
    return paths


def find_session_dir(section_dir: Path, session: str) -> Path | None:
    """Find the child of `section_dir` whose ordinal prefix matches `session` exactly
    (session='3' matches '3_x'/'03_x'/'003_x', but not '13_x' or '30_x')."""
    if not section_dir.is_dir() or not session.isdigit():
        return None
    target = int(session)
    for child in sorted(section_dir.iterdir()):
        if child.is_dir() and session_number(child.name) == target:
            return child
    return None


def discover_local_sessions(repo_root: Path) -> list[str]:
    """The session numbers a CHECKOUT holds, across every discovered section.

    The local-checkout twin of `discovery.discover_sessions`, which asks GitHub for a
    recursive tree instead. A caller that has already cloned the repo to copy files out of
    it has the answer on disk, and the API's copy of it can only be the same or staler."""
    return [
        str(n)
        for n in sorted(
            {n for parent, _, n in session_dirs(_local_dir_paths(repo_root)) if parent}
        )
    ]


def discover_sections(repo_root: Path) -> list[str]:
    """Any top-level directory containing at least one ordinal-prefixed subdirectory is
    a releasable section - no declared config, the directory structure is the only
    source of truth. Sorted for a deterministic order.

    The local-checkout transport of the session_dirs rule; dsl_course.discovery is the
    API-side one."""
    return sorted(
        {parent for parent, _, _ in session_dirs(_local_dir_paths(repo_root)) if parent}
    )
