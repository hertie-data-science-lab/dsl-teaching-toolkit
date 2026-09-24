"""Discover, over the GitHub API, what a course/semester org actually contains.

Everything the workflow renderers need to populate their dropdowns (semester orgs, target
repos, assignment templates, sections/sessions), and everything the site generator needs
to find released content - read live from the orgs themselves, so there is no declared
config to drift out of date.

The semester registry is the one exception to "read it from the live org": semester orgs
can't be found by naming convention (they're arbitrary), so they're listed explicitly in
the course org's .github/semesters.yml (register_semester appends; faculty &
instructors can edit it by hand).

The session-folder rule itself lives in course.session_dirs - this module is only the API
transport for it (a recursive git-tree fetch); course.discover_sections is the local
filesystem transport of the same rule.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import yaml

from . import records
from .central import resolve_central_ref
from .course import (
    CONFIG_REPO,
    COURSE_CONFIG,
    COURSE_HUB_TOPIC,
    GRADEBOOK_PREFIX,
    JOIN_REPO,
    OLD_SEMESTER_TOPIC,
    SEMESTER_TOPIC,
    session_dirs,
)
from .faults import ConfigFault, NotMigrated, Unusable, not_migrated_fault
from .gh_contents import get_file_content, load_yaml_config, put_file, repo_tree
from .ghcli import gh
from .log import log, log_err, log_ok
from .repos import default_branch, repo_exists, repo_is_archived, repo_missing

# The standalone semester registry in the course org's .github repo.
SEMESTERS_PATH = "semesters.yml"
# Its old name and key (decision 0012). Never read: a course that still has the old file
# and not the new one, or the old `cohorts:` key, is refused as NOT_MIGRATED.
OLD_SEMESTERS_PATH = "cohort-courses-pages.yml"

INFRA_REPOS = {JOIN_REPO, CONFIG_REPO, ".github"}
# The topic assign.py stamps on the frozen semester-side template it creates before
# provisioning a single student repo (ensure_semester_template). Named here, and imported by
# the one writer and the one reader, so the string cannot drift between them.
ASSIGNMENT_TEMPLATE_TOPIC = "assignment-template"
# Topics marking a repo as machinery rather than faculty-authored content: per-student
# submission repos and the frozen semester-side assignment templates (assign.py), and the
# private per-student gradebooks (grades.py).
INFRA_TOPICS = {"submission", ASSIGNMENT_TEMPLATE_TOPIC, "gradebook"}
# The repos only a semester org has - the fallback tier signal for an org bootstrapped
# before the topics existed, or whose topic stamp never landed.
SEMESTER_ONLY_REPOS = {JOIN_REPO, CONFIG_REPO}


def join_issue_url(semester_org: str) -> str:
    """Where a student opens a Join course or a Join team issue.

    ONE spelling, because four surfaces point at it - the org profile, the semester site's
    callout, the enrolment-code mail and the team-formation mail - and a semester whose
    join repo moved with one of them left behind is a semester told to go somewhere that
    is not there."""
    return f"https://github.com/{semester_org}/{JOIN_REPO}/issues/new/choose"


def carries_old_semester_topic(repos: list[dict]) -> bool:
    """Whether the org's `.github` still carries the OLD semester topic and not the new
    one (decision 0012): a semester that has not been migrated."""
    dotgithub = next((r for r in repos if r["name"] == ".github"), None)
    topics = set((dotgithub or {}).get("topics") or [])
    return OLD_SEMESTER_TOPIC in topics and SEMESTER_TOPIC not in topics


def org_tier(repos: list[dict]) -> str | None:
    """`"semester"`, `"course"`, or None when the listing cannot say.

    The `.github` repo's topic is authoritative; the semester-only infra repos are the
    fallback. None is a real answer, not "course": a legacy semester (`hertie-dl-f2025`:
    `.github` + student repos, no `join`, no topics) looks exactly like a course org by
    elimination, and the faculty-access sweep treats "course" as "push everywhere"."""
    dotgithub = next((r for r in repos if r["name"] == ".github"), None)
    topics = set((dotgithub or {}).get("topics") or [])
    # The old topic alone is NOT a tier (never read as one): the org's tier cannot be told,
    # so the faculty-access sweep gives it the read floor. Its caller reports the org as
    # NOT_MIGRATED (`carries_old_semester_topic`) and carries on with the rest.
    if carries_old_semester_topic(repos):
        return None
    if SEMESTER_TOPIC in topics:
        return "semester"
    if COURSE_HUB_TOPIC in topics:
        return "course"
    if any(r["name"] in SEMESTER_ONLY_REPOS for r in repos):
        return "semester"
    return None


def classify_repos(repos: list[dict]) -> dict[str, str | None]:
    """`{repo name: the semester assignment template it derives from, or None}`.

    THE submission-repo rule, computed ONCE for a whole listing. A submission repo is
    generated from one of the org's semester assignment templates, so its name is that
    template's name plus a `-<handle>` or `-<team>` suffix.

    Longest template first: `assignment-4` and `assignment-4-project` both prefix
    `assignment-4-project-ada-l`, and only the longer one leaves a suffix that is a handle
    rather than `project-ada-l`. Templates themselves map to None - `assignment-4-project`
    is a repo in this listing AND starts with `assignment-4-`, so a semester holding both
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
    must never be treated as a content repo - a repo this returns False for HOSTS faculty
    workflows and gets the org-admin DSL_BOT_TOKEN set as a repo secret - and a private
    `grades-<handle>` gradebook must never show up as a release target or get tree-scanned
    for sessions. It is not the only route to that token any more: an assignment template
    is excluded here and is equipped, and mirrored, by `discover_assignments` instead.
    """
    name = repo["name"]
    if name in INFRA_REPOS or name.endswith(".github.io"):
        return True
    return _has_infra_topic(repo)


def _has_infra_topic(repo: dict) -> bool:
    """Whether `repo`'s TOPICS mark it machinery - a submission repo, a frozen semester
    assignment template, or a private gradebook. A gradebook is recognised by NAME too:
    the topic is stamped in a separate call after the create, and a failed stamp must not
    put `grades-<handle>` on a public page."""
    if repo["name"].startswith(GRADEBOOK_PREFIX):
        return True
    return bool(set(repo.get("topics") or []) & INFRA_TOPICS)


def list_org_repos(org: str) -> list[dict]:
    """Every repo in `org`, fully paginated - the one repo listing every discovery
    helper here goes through.

    `gh repo list` needs a fixed `--limit`, and a semester org holds a repo per student
    per assignment plus a gradebook each, so any fixed cap silently truncates discovery
    (missing release targets, un-refreshed workflows). `gh api --paginate` walks every
    page instead. `--jq` emits one JSON object per line per page, so the pages are
    parsed as NDJSON rather than concatenated arrays.

    Fields are normalised to the names the callers use (`url`, `isTemplate`).

    `pushed_at` rides along because one listing answering "which of these has moved?" is
    what saves the sheet refresh a commits call per submission repo per tick (see
    `collect._provisional_pins`).

    An empty list means the org genuinely holds no repos; a failed listing raises, since
    every caller reads "no repos" as "nothing to do" (refresh converges zero repos and
    reports success, profile_readme misfiles a semester org as a course org).
    """
    code, out = gh(
        "api",
        "--paginate",
        f"orgs/{org}/repos?per_page=100",
        "--jq",
        ".[] | {name, description, visibility, url: .html_url, "
        "isTemplate: .is_template, archived, pushed_at, topics: (.topics // [])}",
    )
    if code != 0:
        raise RuntimeError(f"could not list repos in {org}: {out[:200]}")
    try:
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"unparseable repo listing for {org}: {out[:200]}") from exc


def listing_by_name(org: str) -> dict[str, dict] | None:
    """`{repo name: its listing row}` for a whole org off ONE paginated listing, or None
    when the listing could not be read.

    The shape every unattended pass wants from `list_org_repos`: "is this repo there, and
    what does GitHub say about it?" asked of a hundred names at once, rather than a GET
    apiece. The scheduler takes one of these per semester at the start of a tick and hands it
    to every pass that asks a question of it (the freeze's `pushed_at`, the receipts'
    `visibility`, the digest's hand-out check, the gradebooks' existence).

    None rather than an exception, and None rather than `{}`: every caller has its own
    answer to "we could not look" - probe that one repo after all, report nothing, assume
    private - and none of them may read a failed listing as an empty org."""
    try:
        return {row["name"]: row for row in list_org_repos(org)}
    except RuntimeError as exc:
        log_err(f"could not list {org}'s repos: {exc}")
        return None


def exists_in(listing: dict[str, dict] | None, org: str, name: str) -> bool:
    """Whether `org/name` is there: off the caller's listing, or - where there is none -
    off a probe of its own.

    One spelling of "the listing knows, unless nobody could take one". `None` is "we could
    not look", never "the org is empty", so it costs a probe rather than a wrong answer;
    an empty dict IS a listing, and a repo missing from it is missing. Four call sites
    wrote this out by hand, and a fifth would have had to get the None right again."""
    if listing is not None:
        return name in listing
    return repo_exists(org, name)


def assignment_rows(listing: dict[str, dict], name: str) -> list[dict]:
    """The LIVE rows of `listing` that the semester assignment `name` handed out: generated
    from its semester-side template, and not archived.

    One rule for a question two sweeps ask - the digest's `visibility:` check and the
    `student_choice` re-privatise pass - which were two spellings of a filter whose whole
    subtlety is in `classify_repos` (a template is not one of its own submissions, and
    `assignment-4` does not own `assignment-4-project-ada-l`).

    Archived rows are left out on both sides: one is read-only, so a write against it 403s
    on every tick for the rest of the term, and a finished semester is meant to stay frozen.
    Pure CPU over rows already in memory, so it is asked per assignment rather than
    computed once and threaded."""
    derived = classify_repos(list(listing.values()))
    return [
        row
        for row in listing.values()
        if derived.get(row["name"]) == name and not row.get("archived")
    ]


def listing_row(org: str, name: str, visibility: str = "private") -> dict:
    """One row for a repo THIS run has just created, in the shape `list_org_repos` gives
    every other row.

    `visibility` is GITHUB's word for the repo as it stands NOW - `private` unless this
    run has already flipped it and been told the flip landed. Never the config's
    `visibility:`: that file says what was ASKED for, this row says what is there, and a
    reader that compares the two (`grades._visibility_faults`) can only notice a
    difference if the row was not written from the same wish.

    A listing is taken once and handed to every pass of a tick, so the pass that CREATES
    a repo is the one thing that can make it stale - and the next pass then creates the
    same repo again and counts GitHub's refusal as a failure. Inserting the row is what
    keeps one listing true for a whole tick.

    Every field a reader of a listing asks for, answered as the repo really is a moment
    after its create: nothing is a template or archived when it is made, it carries no
    topics until `_tag_submission` stamps them, and `pushed_at` is now - the generate IS
    a push, and a row without it reads as "never pushed to", which is the one answer that
    would have the sheet refresh skip a repo rather than look at it."""
    return {
        "name": name,
        "description": "",
        "visibility": visibility,
        "url": f"https://github.com/{org}/{name}",
        "isTemplate": False,
        "archived": False,
        "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topics": [],
    }


def _registry_fault(what: str) -> ConfigFault:
    """The semester registry, unusable - what a human is asked to fix.

    `where` is the file itself, because there is no entry to name: the registry is a flat
    list of org names, so what goes wrong with it is its SHAPE, and the whole course pays
    the same price either way (see `faults.CONSEQUENCE`). `in_repo` is the COURSE org's
    public `.github`, which is what makes the citation and the digest's deep link point at
    the file somebody has to edit rather than at a semester's semester-config."""
    return ConfigFault(
        SEMESTERS_PATH,
        what,
        file=SEMESTERS_PATH,
        field="semesters",
        in_repo=".github",
        # Its own sentence, because every fault this builds is about the whole file and
        # the file's fallback (`faults.FIX`) says "correct the line above" - which names a
        # line that does not exist, under a citation with nothing to link to.
        fix_text=(
            f"restore {SEMESTERS_PATH} in the course org's `.github` as a `semesters:` list "
            f"of this course's semester org names, one per line"
        ),
    )


def _read_semesters(
    course_org: str, faults: list[ConfigFault] | None = None
) -> list[str]:
    """Read the course org's standalone .github/semesters.yml registry.

    A genuinely absent or empty registry is [] (a valid brand-new course org). The
    machine-written form is a `{semesters: [...]}` mapping, but the file is human-editable
    and a bare top-level list has always been accepted too. Anything else - YAML that does
    not parse, a scalar, or a semester list that isn't all strings - is malformed, logged and
    raised, never silently flattened to [] (which downstream renders every dropdown as
    "(none-yet)" and lets a whole-course sync go quietly green).

    `faults` collects the same two verdicts for the notifier INSTEAD of raising them: a
    caller that passes one is asking what is wrong with the file so it can tell somebody,
    not asking for a list of semesters it is about to act on. Everything else still raises,
    because a registry nobody can read is a registry nothing may be pruned against - as
    `faults.Unusable`, which says this is a file faculty must fix rather than a read that
    failed, so an unattended run can skip it and stay green."""
    content = get_file_content(course_org, ".github", SEMESTERS_PATH)
    if content is None and get_file_content(course_org, ".github", OLD_SEMESTERS_PATH):
        return _not_migrated(course_org, OLD_SEMESTERS_PATH, SEMESTERS_PATH, faults)
    if not content:
        return []
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        # Unparseable is malformed, exactly like the shape check below - and the bare
        # safe_load surfaced it as a raw PyYAML traceback from wherever the registry
        # happened to be read, naming a "<unicode string>" rather than the file.
        msg = f"malformed semester registry in {course_org}/.github/{SEMESTERS_PATH}: {exc}"
        log_err(msg)
        if faults is None:
            raise Unusable(msg) from exc
        faults.append(
            _registry_fault(
                "this file is not valid YAML, so no semester under this course is synced"
            )
        )
        return []
    if isinstance(data, dict) and "cohorts" in data and "semesters" not in data:
        return _not_migrated(course_org, "cohorts", "semesters", faults)
    semesters = data.get("semesters", []) if isinstance(data, dict) else data
    if not isinstance(semesters, list) or not all(
        isinstance(c, str) for c in semesters
    ):
        msg = (
            f"malformed semester registry in {course_org}/.github/{SEMESTERS_PATH}: "
            f"expected a list of semester org names (bare, or under a 'semesters:' key)"
        )
        log_err(msg)
        if faults is None:
            raise Unusable(msg)
        faults.append(
            _registry_fault(
                "this is not a list of semester org names (bare, or under a `semesters:` "
                "key), so no semester under this course is synced"
            )
        )
        return []
    return [c for c in semesters if c]


def _not_migrated(
    course_org: str, old: str, new: str, faults: list[ConfigFault] | None
) -> list[str]:
    """An old spelling in the registry: raised, or - for a caller collecting faults -
    filed, and read as no semesters at all."""
    if faults is None:
        raise NotMigrated(old, new, f"{course_org}/.github/{SEMESTERS_PATH}")
    faults.append(
        not_migrated_fault(
            old, new, where=SEMESTERS_PATH, file=SEMESTERS_PATH, in_repo=".github"
        )
    )
    return []


def read_semester_registry(course_org: str, faults: list[ConfigFault]) -> list[str]:
    """This course's registered semesters, with what a human must fix collected rather than
    raised.

    The fault-collecting twin of `discover_semesters`, and it draws the same line
    `sync_faculty.read_semester_people` draws: a file that is MALFORMED is a fault, because
    every semester under this course stops being reconciled and that is something a course
    admin has to fix; a read that FAILED still raises, because "we could not look" must
    never be reported to faculty as "your file is broken"."""
    return _read_semesters(course_org, faults)


def org_meta(org: str) -> dict:
    """An org's `.github/dsl-course.yml`, or `{}` when it declares none.

    THE read for an org's declared identity - the course name, the faculty SSOT, a
    semester's `course:` pointer, the `central_ref:` its workflows run. `{}` for a genuine
    404 or an empty file; a MALFORMED one still raises, because reading a typo as "this
    org declares nothing" files a semester under the course orgs and rewrites the inventory
    around it. The one caller that must tell ABSENT from EMPTY - sync_faculty, which
    would otherwise prune every admin - reads load_yaml_config directly."""
    return load_yaml_config(org, ".github", COURSE_CONFIG) or {}


def semester_pointer(semester_org: str) -> dict:
    """A semester's pointer to its course org (`semester-config/.system/dsl-course.yml`),
    or `{}` when there is none. Moved in from the semester's `.github` (decision 0010):
    its only readers are this repo's dispatchers and the engine, and `.github` is public."""
    return load_yaml_config(semester_org, CONFIG_REPO, records.path("pointer")) or {}


def course_name_for_semester(semester_org: str) -> str:
    """This semester's course name, for student-facing prose ("your grades for X").

    Follows the semester's own `course:` pointer (`semester_pointer`) to its course
    org, then reads that org's identity file - the same two hops status.collect makes,
    but starting from the semester, which is all an emailer is given.

    Returns "" when either file is missing or carries no name, so callers fall back to
    generic wording. A student must never be emailed a blank or a literal placeholder
    where the course name belongs.
    """
    return course_name_of(course_org_for_semester(semester_org))


def course_org_for_semester(semester_org: str) -> str:
    """The COURSE org this semester belongs to, from its own `course:` pointer
    (`semester_pointer`). "" when the pointer is missing or unreadable.

    A semester-side CLI is given only the semester; anything it needs from the course side -
    an assignment's `grading_config.yml`, say - has to start here."""
    return str(semester_pointer(semester_org).get("course") or "")


def course_name_of(course_org: str) -> str:
    """A COURSE org's display name from its own identity file. "" when unnamed or absent.

    The toolkit's single spelling of that fallback - `course_name`, else `org_name` - so a
    semester landing page, a status row and an email cannot disagree about what a course is
    called. Takes "" and returns "" so a caller holding a semester pointer that names no
    course org needs no guard of its own."""
    if not course_org:
        return ""
    meta = org_meta(course_org)
    return str(meta.get("course_name") or meta.get("org_name") or "")


def central_ref_for(org: str) -> str:
    """Which ref of the central toolkit this org's seeded workflows run the engine from.

    Declared as `central_ref:` in the COURSE org's `.github/dsl-course.yml`, so one edit
    moves a course and every semester under it between tiers together. A semester org has
    no `dsl-course.yml` of its own, only the pointer (`semester_pointer`), so this follows
    it - a semester running a different engine from the course org that releases into it
    is not a state anyone wants to debug.

    Absent means `central.CENTRAL_REF`; a value that is neither a tier nor a full SHA
    raises `central.MissingCentralRef` - see resolve_central_ref."""
    meta = org_meta(org)
    course = "" if meta else course_org_for_semester(org)
    if course:
        org, meta = course, org_meta(course)
    return resolve_central_ref(
        meta.get("central_ref"), source=f"{org}/.github/{COURSE_CONFIG}"
    )


def discover_semesters(course_org: str) -> list[str]:
    """Semester orgs are listed explicitly in the course's .github/semesters.yml
    (naming-independent). `bootstrap --semester --course X` appends; faculty & instructors can edit it."""
    return sorted(_read_semesters(course_org))


def semester_is_live(semester_org: str) -> bool:
    """Whether `semester_org` is still running, rather than closed out and left frozen.

    An archived `semester-config` IS the "this semester is finished" marker - it is the last
    thing `teardown` freezes, for exactly that reason - and everything a course-side sweep
    would do to a finished semester is a write into a read-only org: every one of them 403s,
    every night, for the rest of the course's life. A finished term is a state somebody
    chose, so it is a line rather than an error.

    Says so once, here, so the six sweeps that skip such a semester cannot word it six ways.
    `repos.repo_is_archived` fails OPEN, so "could not tell" reads as LIVE: guessing that
    way costs one failed write that says so out loud, and guessing the other way silently
    stops syncing a semester mid-term."""
    if repo_is_archived(semester_org, CONFIG_REPO):
        log(f"  [skip] {semester_org} (archived semester - left frozen)")
        return False
    # No config repo under its name at all, and the old topic: a semester archived before
    # decision 0010 (never migrated, never touched) or one the migration has not reached.
    # Either way nothing may be written into it.
    if repo_missing(semester_org, CONFIG_REPO) and not_migrated_org(semester_org):
        log(f"  [skip] {semester_org} (not migrated - archived, or run the migration)")
        return False
    return True


def not_migrated_org(org: str) -> bool:
    """Whether `org`'s `.github` carries the OLD semester topic and not the new one, read
    from its topics. The listing form, for a caller that already holds one, is
    `carries_old_semester_topic`."""
    code, out = gh("api", f"repos/{org}/.github/topics", "--jq", ".names[]")
    topics = set(out.split()) if code == 0 else set()
    return OLD_SEMESTER_TOPIC in topics and SEMESTER_TOPIC not in topics


def live_semesters(course_org: str) -> list[str]:
    """This course's registered semesters, minus the ones that have been closed out.

    What every course-side sweep that WRITES into its semesters iterates - the scheduler,
    the faculty and membership syncs, the site build. `discover_semesters` stays the answer
    to "which semesters does this course own?", which is a question about the registry and
    not about whether a term is over: a finished semester is still registered, still on the
    course profile page, and still refuses a dispatch that names somebody else's org."""
    return [c for c in discover_semesters(course_org) if semester_is_live(c)]


def register_semester(course_org: str, semester_org: str) -> bool:
    """Append semester_org to the course's semesters.yml registry (idempotent).

    Returns True if the semester is registered afterwards (already present, or the write
    succeeded), False if the write failed - so bootstrap doesn't claim a semester was
    registered when the put_file actually failed."""
    semesters = set(_read_semesters(course_org))
    if semester_org in semesters:
        log_ok(f"{semester_org} already in {course_org}/.github/{SEMESTERS_PATH}")
        return True
    return _write_semesters(
        course_org,
        semesters | {semester_org},
        f"registry: add semester {semester_org}",
        failure=(
            f"failed to register {semester_org} under {course_org}: the registry write "
            f"to {SEMESTERS_PATH} failed"
        ),
        success=f"registered {semester_org} under {course_org}",
    )


def _write_semesters(
    course_org: str, semesters: set[str], commit: str, *, failure: str, success: str
) -> bool:
    """Serialise the registry and write it back, reporting either way. The one place the
    file's SHAPE is decided, so the two callers that edit it cannot disagree about it -
    and the one place a write failure is turned into a False, so neither can claim an edit
    that did not land."""
    body = yaml.safe_dump({"semesters": sorted(semesters)}, sort_keys=False)
    if not put_file(course_org, ".github", SEMESTERS_PATH, body.encode(), commit):
        log_err(failure)
        return False
    log_ok(success)
    return True


def unregister_semester(course_org: str, semester_org: str) -> bool:
    """Drop semester_org from the course's registry (idempotent), and NOT the mirror image
    of `register_semester`: the registry is APPEND-ON-INTENT, PRUNE-ON-REALITY.

    Adding stays a deliberate act, because a semester's absence can be intended - a faculty
    member may unregister one to stop its nightly syncs. Removal cannot: a semester dropped
    from here is invisible to every nightly sync, which is a SILENT no-op, where a stale
    entry merely fails loudly once a night. So the liveness verdict belongs to the caller
    (`seed._live_semesters`) and this only writes down what it was told.

    Returns True if the semester is absent from the registry afterwards."""
    semesters = set(_read_semesters(course_org))
    if semester_org not in semesters:
        return True
    return _write_semesters(
        course_org,
        semesters - {semester_org},
        f"registry: drop deleted semester {semester_org}",
        failure=(
            f"failed to unregister the deleted org {semester_org} from {course_org}: the "
            f"registry write to {SEMESTERS_PATH} failed - every sync will keep trying it"
        ),
        success=f"unregistered {semester_org} from {course_org} (the org no longer exists)",
    )


def semester_content_repos(repos: list[dict]) -> list[str]:
    """Candidate target repos in a semester LISTING: real content repos, excluding
    everything _is_infra_repo covers (infra, the website, submission repos, assignment
    templates, gradebooks). Only what genuinely exists - no placeholder default, so an org
    with nothing registered yet correctly shows an empty (not phantom) dropdown.

    Takes the listing rather than the org, because its one caller (the semester site build)
    asks two questions of the same org and paid for two full paginated listings to do it."""
    return sorted(r["name"] for r in repos if not _is_infra_repo(r))


def _repo_tree_dirs(org: str, repo: str) -> tuple[str, ...]:
    """Every directory path in a repo's default-branch tree - one recursive fetch,
    shared by every discovery helper that needs a repo's directory structure (rather
    than listing each top-level directory individually - N+1 API calls).

    The fetch itself (and its absent-vs-failed discrimination) is gh_contents.repo_tree, shared
    with the site builder's blob-side twin: an absent/empty tree is genuinely no
    directories, any other failure RAISES. It must never come back as "no sessions" - the
    site clears and rewrites its collections from these rows, so one rate-limited fetch
    would republish the semester site with every session row deleted."""
    return repo_tree(org, repo, default_branch(org, repo, fallback="main"), "tree")


def discover_release_sources(
    org: str, content_repos: list[str]
) -> list[tuple[str, str, str, int]]:
    """(repo, subpath, folder_name, session_number) for every session folder found
    across a semester's `content_repos` (see discover_semester_repos), covering both shapes
    a release can produce: nested - a session folder inside a subpath of a shared repo,
    `subpath/NN_.../` - or root - `NN_.../` directly at the repo root (what a `deploy:`
    with a bare `semester_dest_repo` and no `semester_dest_path` produces). One recursive tree fetch per
    repo; the exact folder name is captured too, so callers can list its files directly
    with no further discovery call."""
    return [
        (repo, subpath, folder, n)
        for repo in content_repos
        for subpath, folder, n in session_dirs(_repo_tree_dirs(org, repo))
    ]


def discover_assignment_repos(course_org: str) -> list[dict]:
    """The listing ROW of every assignment template in the course org, in name order.

    Two different questions are asked of these repos - which of them a dropdown offers,
    and which of them a refresh may WRITE to - and the row carries what tells the two
    apart (`archived`). One listing answers both."""
    return sorted(
        (
            r
            for r in list_org_repos(course_org)
            if r["name"].startswith("assignment-") and r.get("isTemplate")
        ),
        key=lambda r: r["name"],
    )


def discover_assignments(course_org: str) -> list[str]:
    """Assignment template repos in the course org (named assignment-*) - the dropdown.

    ALL of them, archived included: a finished assignment is a legitimate source to copy
    next year's forward from, and a dropdown only ever offers a repo to READ."""
    return [r["name"] for r in discover_assignment_repos(course_org)]


def handed_out_assignments(repos: list[dict]) -> frozenset[str]:
    """The semester-side name of every assignment this semester has ACTUALLY been given.

    assign.py's stage 1 freezes a semester-level template repo named exactly the semester-side
    name (`schedule.semester_name` - the slug unless `semester_dest_repo` renames it) and
    topics it `assignment-template`, before it provisions a single student repo
    (`ensure_semester_template`). So that repo existing IS the semester-side record that the
    hand-out happened, whatever route fired it - the scheduled pin, the manual workflow, or a
    `releases:` entry's `assignment:`.

    The site gates an assignment's brief on this (see `site._assignment_entry`), which is
    why it reads what SHIPPED rather than what the plan intended: a hand-out with no
    `handout_datetime` pinned - the manual workflow's documented mode - is invisible to the
    plan, and gating on the plan alone published those briefs on sight.

    Takes the LISTING, shared with `semester_content_repos` by the site build that asks both
    of the same org. SHARED, never memoised: a process-wide memo of `list_org_repos` would
    serve the site a listing taken BEFORE assign.py created the template repo it then syncs
    the site for, withholding the brief it had just handed out."""
    return frozenset(
        r["name"] for r in repos if ASSIGNMENT_TEMPLATE_TOPIC in (r.get("topics") or [])
    )


def discover_content_repos(course_org: str) -> list[str]:
    """Repos a materials release can come OUT of: the materials repo(s), not the infra
    repos (_is_infra_repo - notably NOT the public `<org>.github.io` site repo, which
    would otherwise be handed the org-admin token as a repo secret) and not the
    assignment-* template repos, which hold a brief and a starter rather than the session
    folders a release copies - and which every student repo is GENERATED FROM, so what one
    of them hosts has to be stripped off the semester copy first
    (`assign.withhold_from_template`) rather than placed and forgotten.

    So this is the Release materials SOURCE dropdown, and the repos that host that button.
    It is NOT "every repo with a run-from-repo workflow": an assignment template hosts
    Release assignment, and `discover_assignments` is the list of those."""
    return sorted(
        r["name"]
        for r in list_org_repos(course_org)
        if not _is_infra_repo(r) and not r["name"].startswith("assignment-")
    )
