"""dsl-course sync-faculty -- materialise course-admin (course-wide) and
instructors/TAs (per-cohort) team membership.

Two independent flows, split by role rather than by "stability":

- `course_admins` - genuinely course-wide (course director / permanent admin, needs
  admin rights everywhere). Declared ONCE on the persistent COURSE org's
  `.github/dsl-course.yml` `people:` block - the SSOT, reconciled into the course
  org's own `course-admin` team AND mirrored into every cohort org's own
  `course-admin` team. Unchanged from the original course-org-SSOT design.
- `instructors`/`teaching_assistants` - genuinely cohort-scoped (most cohorts have
  different lecturers/TAs). Declared PER COHORT, in that cohort's own
  `classroom-config/people.yml` (see `load_cohort_faculty`) - reconciled into that
  cohort's own `instructors` team, AND synced UP into a parallel, tag-scoped
  `instructors-<tag>` team on the COURSE org (push access on just that tag's
  content repos, PLUS the central `.github` repo so its members can also use the
  central dispatch workflows), so a cohort's own people can push materials without a
  course-level declaration. No merge/union across cohorts - each cohort's tag gets
  its own team, so there's no "which cohort wins" ambiguity and no
  accumulate-forever list.

Each person entry requires `github_handle` (the only field that grants access) and, for
an instructor or a TA, `email` (the only way a notification reaches them - see
`teaching_contacts`); `start`/`end` (optional ISO dates) bound when they're active,
giving auto-rotation with no manual removal step. Every reconcile here is FULL
(add + remove) - a lapsed `end` date or a deleted entry revokes access on the next sync,
same as an edit to students.csv/teams.csv.

A missing or malformed `email` is reported and does NOT withhold access: a cohort that
has not filled it in keeps working (and keeps getting the @mention on its digest issue)
rather than losing its team on the next sync.

Usage:
    python3 -m dsl_course.sync_faculty --course-org hertie-dsl-demo-course-e1234
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from functools import cache
from typing import NamedTuple

import yaml

from .access import grant_team_repo_access
from .central import MissingCentralRef, resolve_central_ref
from .course import (
    CONFIG_REPO,
    COURSE_ADMIN_TEAM,
    COURSE_CONFIG,
    INSTRUCTORS_TEAM,
    active_today,
    term_tag,
)
from .discovery import (
    discover_assignments,
    discover_cohorts,
    discover_content_repos,
)
from .faults import ConfigFault
from .gh_contents import (
    get_file_content,
    line_of,
    load_yaml_config,
    load_yaml_lines,
    take_lines,
)
from .gh_teams import create_team, is_valid_github_username, reconcile_team_members
from .log import log, log_err, log_ok, log_step

ROLE_TEAM = {
    "instructors": INSTRUCTORS_TEAM,
    "teaching_assistants": INSTRUCTORS_TEAM,
    "course_admins": COURSE_ADMIN_TEAM,
}
COHORT_PEOPLE_PATH = "people.yml"
# The roles a cohort declares: its teaching team, the people a notification is addressed
# to and therefore the entries `email:` is required on. course_admins is course-level and
# notified through the course org, not a cohort's people.yml.
TEACHING_ROLES = ("instructors", "teaching_assistants")


# --------------------------------------------------------------------------- pure core


def valid_email(value: object) -> str | None:
    """A declared address as written, or None when it is absent or cannot be one (no `@`
    with something either side). NEVER log the return value or the input: an address is
    personal data and every faculty workflow runs in a public repo. Report the role and
    the handle instead."""
    text = str(value or "").strip()
    local, _, domain = text.partition("@")
    return text if local and domain else None


def _people_fault(
    role: str,
    index: int,
    field: str,
    what: str,
    lines: dict[str, int],
    file: str,
    repo: str,
) -> ConfigFault:
    """One entry of a people block that the sync cannot use as written.

    The entry is named by its ROLE and its position, not by its handle: `where` is this
    fault's identity in the digest's state and its heading in the mail, and an entry
    somebody renames is not a new fault. The line is what sends anybody to it - and
    `repo`, with `file`, is what makes that line a place: the same block is a cohort's
    `classroom-config/people.yml` and a course org's `.github/dsl-course.yml`."""
    return ConfigFault(
        f"people.{role}[{index}]",
        what,
        file=file,
        field=field,
        in_repo=repo,
        lineno=line_of(lines, field),
    )


def parse_faculty_from_meta(
    meta: dict,
    faults: list[ConfigFault] | None = None,
    file: str = COHORT_PEOPLE_PATH,
    repo: str = CONFIG_REPO,
) -> dict[str, list[dict]]:
    """Parse an already-loaded config mapping's `people:` block (course org's
    dsl-course.yml, or a cohort's people.yml - same schema) for the roles in ROLE_TEAM.
    Only entries with a `github_handle` grant access; a named entry without one is a
    legitimate display-only card (noted, not an error), anything else is junk (flagged).

    An instructor/TA entry with no usable `email:` is an error line naming the role and
    the handle - the entry still grants access, it just cannot be notified.

    `faults` collects the same three things for the notifier: an entry with no handle to
    grant anything to, a handle that cannot be a GitHub username (adding it to a team
    would INVITE it, so the sync skips it), and a teaching entry no notification can
    reach. `file` and `repo` name where they are - the same schema is a cohort's
    `classroom-config/people.yml` and a course org's `.github/dsl-course.yml`, and a fault
    that cited the wrong one would link a reader at a file that does not exist.

    The line stamps (`take_lines`) are consumed here whether or not anybody asked for
    faults, so no consumer downstream can ever render the loader's reserved key."""
    people = meta.get("people")
    if not isinstance(people, dict):
        return {}
    take_lines(people)
    faculty: dict[str, list[dict]] = {}
    for role in ROLE_TEAM:
        entries = []
        for index, p in enumerate(people.get(role) or []):
            lines = take_lines(p) if isinstance(p, dict) else {}
            if isinstance(p, dict) and p.get("github_handle"):
                entries.append(p)
                # Not logged here: `desired_team_members` says it, in the place that
                # acts on it. This is the same fact on the channel that reaches somebody
                # who is not reading a cron's log.
                if faults is not None and not is_valid_github_username(
                    str(p["github_handle"])
                ):
                    faults.append(
                        _people_fault(
                            role,
                            index,
                            "github_handle",
                            "this is not a valid GitHub username - the entry is "
                            "skipped, so it grants no access (adding it to a team "
                            "would invite an arbitrary account to the org)",
                            lines,
                            file,
                            repo,
                        )
                    )
                # A COHORT's file only. `email:` is what a notification is addressed to,
                # and only a cohort declares people who are notified through their entry:
                # a course org's dsl-course.yml holds course_admins (mailed through the
                # `DSL_COURSE_ADMIN_EMAILS` org secret, never from a public file) and
                # display-only website cards, neither of which carries one.
                if (
                    role in TEACHING_ROLES
                    and file == COHORT_PEOPLE_PATH
                    and valid_email(p.get("email")) is None
                ):
                    log_err(
                        f"  ! {role} entry {p['github_handle']} has no usable `email:` "
                        f"- it is required (access still granted, but this person is "
                        f"not notified): see {COHORT_PEOPLE_PATH}"
                    )
                    if faults is not None:
                        faults.append(
                            _people_fault(
                                role,
                                index,
                                "email",
                                "no usable `email:` - access is still granted, but no "
                                "notification reaches this person",
                                lines,
                                file,
                                repo,
                            )
                        )
            elif isinstance(p, dict) and p.get("name"):
                log(
                    f"  ({role} entry '{p['name']}' has no github_handle - "
                    f"display-only, no access granted)"
                )
            else:
                log_err(f"  ! skipping {role} entry with no github_handle: {p!r}")
                if faults is not None:
                    faults.append(
                        _people_fault(
                            role,
                            index,
                            "github_handle",
                            "this entry has no `github_handle:` - it grants no access "
                            "and appears nowhere",
                            lines,
                            file,
                            repo,
                        )
                    )
        faculty[role] = entries
    return faculty


def desired_team_members(
    faculty: dict[str, list[dict]], today: str
) -> dict[str, set[str]]:
    """Flatten active entries (per `today`, an ISO date string) via ROLE_TEAM ->
    {'instructors': {handles}, 'course-admin': {handles}}."""
    desired: dict[str, set[str]] = {team: set() for team in set(ROLE_TEAM.values())}
    for role, entries in faculty.items():
        team = ROLE_TEAM[role]
        for p in entries:
            if not active_today(p.get("start"), p.get("end"), today):
                continue
            handle = p["github_handle"]
            # Adding a faculty handle to a team also INVITES it to the org, so a typo'd
            # handle would invite a stranger with push on `.github`. There is no roster to
            # intersect faculty against, so charset-validate at minimum and skip anything
            # that can't be a real GitHub username rather than inviting it.
            handle = str(handle)  # an unquoted YAML handle may parse to int/bool
            if not is_valid_github_username(handle):
                log_err(
                    f"  ! {role} github_handle {handle!r} is not a valid GitHub "
                    f"username - skipping (not inviting)"
                )
                continue
            desired[team].add(handle)
    return desired


def _desired_for(faculty: dict[str, list[dict]], team: str, today: str) -> set[str]:
    """This team's desired active handles from a parsed faculty dict."""
    return desired_team_members(faculty, today).get(team, set())


def _active_teaching_entries(
    faculty: dict[str, list[dict]], today: str
) -> list[tuple[str, dict]]:
    """`(role, entry)` for every parsed instructor/TA entry active on `today` (an ISO date
    string), in declaration order - this cohort's teaching team as it stands today.

    The role rides along because who gets COPIED on a notification depends on it: a mail
    addressed to a TA copies the instructors. Reading it back off the entry afterwards
    would mean iterating people.yml a second way."""
    return [
        (role, p)
        for role in TEACHING_ROLES
        for p in faculty.get(role) or []
        if active_today(p.get("start"), p.get("end"), today)
    ]


class Contact(NamedTuple):
    """One reachable member of a cohort's teaching team.

    All three fields travel together because a notification needs all three: the handle is
    what git blame and an @mention speak, the address is what the mail uses, and the role
    decides who is copied."""

    handle: str
    email: str
    role: str

    @property
    def is_ta(self) -> bool:
        return self.role == "teaching_assistants"


def teaching_contacts(faculty: dict[str, list[dict]], today: str) -> list[Contact]:
    """The instructors and TAs active on `today` (an ISO date string) that a notification
    can actually reach, in declaration order.

    A parsed faculty dict and a clock, exactly like `without_email`: the caller reads
    people.yml once and both answers are about the same tick. Taking the raw `people:`
    mapping instead meant a second parse - and a second `date.today()`, which is not the
    clock a scheduler run is reasoning about.

    Entries with no usable `email:` are left out - `parse_faculty_from_meta` has already
    reported them and `without_email` names the handles. Log a length, or a handle through
    `log_person`; never an address."""
    out: list[Contact] = []
    for role, p in _active_teaching_entries(faculty, today):
        email = valid_email(p.get("email"))
        if email:
            out.append(Contact(str(p["github_handle"]), email, role))
    return out


def without_email(faculty: dict[str, list[dict]], today: str) -> list[str]:
    """The handles of active instructors/TAs no notification can reach. Handles, which
    are public and loggable; the addresses themselves never leave this module."""
    return [
        str(p["github_handle"])
        for _role, p in _active_teaching_entries(faculty, today)
        if valid_email(p.get("email")) is None
    ]


def _cohort_roles_only(faculty: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """A cohort's people.yml declares instructors/TAs only - course_admins stays
    exclusively course-level, so drop it even if someone puts it there."""
    return {
        role: entries for role, entries in faculty.items() if role != "course_admins"
    }


def _matches_tag(repo: str, tag: str) -> bool:
    """Whether `repo` belongs to this year's tag (e.g. `course-materials-f2026`,
    `assignment-1-f2026` both match `f2026`)."""
    return repo.endswith(f"-{tag}")


def _tag_repos(content_repos: list[str], assignments: list[str], tag: str) -> list[str]:
    """Repos matching `tag` from the course org's already-discovered content/
    assignment repos, plus the central `.github` repo - what `instructors-<tag>`
    needs push access to so its members can use both the run-from-repo and central
    dispatch workflows (`.github` is cross-cohort infrastructure, not itself
    tag-scoped)."""
    matching = [r for r in content_repos if _matches_tag(r, tag)] + [
        r for r in assignments if _matches_tag(r, tag)
    ]
    return [".github"] + matching


# ---------------------------------------------------------------------- gh/git wiring


def load_faculty(course_org: str) -> dict[str, list[dict]] | None:
    """Fetch + parse the course org's `.github/dsl-course.yml` `people:` block -
    course_admins only in practice; instructors/TAs are declared per cohort
    (see `load_cohort_faculty`), but any stray entries here are still parsed
    (and reconciled) the same way `parse_faculty_from_meta` always has.

    Returns None when dsl-course.yml is genuinely ABSENT - the caller must then NOT prune
    (an absent config is not an empty desired set). A present-but-empty `people:` block
    parses to {} and legitimately empties the team."""
    meta = load_yaml_config(course_org, ".github", "dsl-course.yml")
    if meta is None:
        return None
    return parse_faculty_from_meta(meta)


@cache
def load_cohort_faculty(cohort_org: str) -> dict[str, list[dict]] | None:
    """Fetch + parse this cohort's own classroom-config/people.yml - instructors/TAs
    only (no course_admins key here; that role stays exclusively course-level).

    Returns None when people.yml is genuinely ABSENT (do not prune); a present-but-empty
    people block parses to {} and legitimately empties the team.

    THE door to a cohort's people.yml, and memoised per process like `repos._repo`: a
    release tick asks it who to notify, `status` asks it who is unreachable, and the file
    changes only when somebody edits it. The memo also means the "no usable `email:`"
    error lines are printed once per run rather than once per reader.
    `tests/conftest.py` clears it."""
    meta = load_yaml_config(cohort_org, CONFIG_REPO, COHORT_PEOPLE_PATH)
    if meta is None:
        return None
    return _cohort_roles_only(parse_faculty_from_meta(meta))


def read_cohort_people(
    cohort_org: str, faults: list[ConfigFault]
) -> dict[str, list[dict]] | None:
    """This cohort's people.yml, parsed, with everything a human must fix collected.

    The fault-collecting twin of `load_cohort_faculty`, and deliberately NOT memoised: it
    reads the file with line stamps (which the cached loader must not hand to the site
    renderer) and it is asked once per tick.

    A file that is ABSENT or that does not parse is a fault of its own rather than an
    exception, because both mean the same thing to a cohort - nobody is granted access and
    nobody is notified - and neither is anything a release run should stop for. A read
    that FAILED (a rate limit, a token that lost its scope) still raises: "we could not
    look" must never be reported to faculty as "your file is broken"."""
    try:
        meta = load_yaml_config(cohort_org, CONFIG_REPO, COHORT_PEOPLE_PATH, lines=True)
    except yaml.YAMLError:
        faults.append(_file_fault("this file is not valid YAML, so none of it is read"))
        return None
    except RuntimeError:
        # `load_yaml_config` has already said which file and what it got.
        faults.append(
            _file_fault("this file is not a YAML mapping, so none of it is read")
        )
        return None
    if meta is None:
        faults.append(
            _file_fault(
                "this file is missing, so the cohort has no declared teaching team"
            )
        )
        return None
    return _cohort_roles_only(parse_faculty_from_meta(meta, faults))


def _file_fault(what: str) -> ConfigFault:
    """people.yml as a whole, unusable - no entry to name and no line to point at."""
    return ConfigFault(
        COHORT_PEOPLE_PATH,
        f"{what} - no instructor or TA is granted access or notified",
        file=COHORT_PEOPLE_PATH,
        field="people",
        fix_text=(
            f"restore {COHORT_PEOPLE_PATH} from the template and declare the cohort's "
            f"instructors and teaching assistants in it"
        ),
    )


def _course_fault(what: str, field: str = "", lineno: int | None = None) -> ConfigFault:
    """One thing in the COURSE org's identity file the sync cannot use.

    `in_repo` is the course org's public `.github`, which is what puts the citation, the
    deep link and the blame query on the file somebody has to edit. `where` is the file
    for a fault about the whole of it and the file plus the key for a fault about one
    line, so the two can never collide in the digest's state."""
    return ConfigFault(
        COURSE_CONFIG,
        what,
        file=COURSE_CONFIG,
        field=field,
        in_repo=".github",
        lineno=lineno,
    )


def read_course_config(
    course_org: str, faults: list[ConfigFault]
) -> dict[str, list[dict]] | None:
    """The COURSE org's `.github/dsl-course.yml`, parsed, with everything a human must fix
    collected. None when the file could not be used at all.

    The fault-collecting twin of `load_faculty`, and deliberately not memoised for the
    same reason `read_cohort_people` is not: it reads the file with line stamps and it is
    asked once per tick.

    Three things in this file stop the whole course being reconciled, and all three come
    back as faults rather than as exceptions - an absent or unparseable file, an admin
    handle no team can be given, and a `central_ref:` no workflow can be pinned to. A read
    that FAILED still raises, because "we could not look" must never be reported to a
    course admin as "your file is broken"."""
    # Read and parsed here rather than through `load_yaml_config`, which turns a malformed
    # file and a read that failed into the same RuntimeError. That distinction is the whole
    # contract of this function, and it is not one a message match can be trusted with.
    content = get_file_content(course_org, ".github", COURSE_CONFIG)
    if content is None:
        faults.append(
            _course_fault(
                "this file is missing, so the course declares no admins and no tier"
            )
        )
        return None
    try:
        meta = load_yaml_lines(content)
    except yaml.YAMLError as exc:
        log_err(f"malformed YAML in {course_org}/.github/{COURSE_CONFIG}: {exc}")
        faults.append(
            _course_fault("this file is not valid YAML, so none of it is read")
        )
        return None
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        log_err(
            f"{course_org}/.github/{COURSE_CONFIG} is not a YAML mapping "
            f"(got {type(meta).__name__}) - refusing to use it"
        )
        faults.append(
            _course_fault("this file is not a YAML mapping, so none of it is read")
        )
        return None
    # Taken before the people block is parsed, so the top-level keys' lines are in hand
    # for `central_ref:` - and so the loader's reserved key cannot survive into anything
    # that renders this mapping.
    lines = take_lines(meta)
    faculty = parse_faculty_from_meta(meta, faults, file=COURSE_CONFIG, repo=".github")
    try:
        resolve_central_ref(
            meta.get("central_ref"), source=f"{course_org}/.github/{COURSE_CONFIG}"
        )
    except MissingCentralRef:
        # Not the exception's own message: it is written for a run log and names the file
        # again, which every surface here has already done. What a reader needs is what it
        # COSTS - `pin_central_ref` refuses the render, so the org keeps whatever workflows
        # it last had and takes no fix or improvement until this line is corrected.
        faults.append(
            _course_fault(
                "`central_ref:` is not `main`, `release` or a full 40-character commit "
                "SHA - every workflow this course seeds stays at its previous rendering "
                "until it is corrected",
                field="central_ref",
                lineno=line_of(lines, "central_ref"),
            )
        )
    return faculty


def sync_course_admins(
    course_org: str, cohorts: list[str], dry_run: bool = False
) -> int:
    """course_admins: declared once on the course org, mirrored unchanged into the
    course org itself and every cohort's own course-admin team."""
    faculty = load_faculty(course_org)
    if faculty is None:
        # ABSENT dsl-course.yml: parsing it as "" would yield an empty desired set and a
        # pruning reconcile would strip course-admin from EVERY org. Refuse to prune.
        log_err(
            f"course identity {course_org}/.github/dsl-course.yml is absent - refusing "
            f"to reconcile course-admin (an absent config would prune every admin from "
            f"{1 + len(cohorts)} org(s)); skipping"
        )
        return 1
    desired = _desired_for(faculty, COURSE_ADMIN_TEAM, date.today().isoformat())
    errors = 0
    for org in [course_org] + cohorts:
        errors += reconcile_team_members(
            org, COURSE_ADMIN_TEAM, desired, prune=True, dry_run=dry_run
        )
    return errors


def sync_cohort_instructors(
    course_org: str,
    cohort_org: str,
    content_repos: list[str],
    assignments: list[str],
    dry_run: bool = False,
) -> int:
    """instructors/TAs: declared in this cohort's own classroom-config/people.yml,
    reconciled into that cohort's own `instructors` team AND a parallel, tag-scoped
    `instructors-<tag>` team on the course org - no merge with any other cohort.
    `content_repos`/`assignments` are the course org's discovered repos, passed in
    (rather than re-discovered here) so a multi-cohort `sync()` fetches them once,
    not once per cohort."""
    faculty = load_cohort_faculty(cohort_org)
    if faculty is None:
        # ABSENT people.yml: reconciling an empty desired set with prune=True would strip
        # this cohort's instructors team (and its course-org tag team). Refuse to prune -
        # and stay GREEN, because a file faculty have to write is a CONTENT fault. It is
        # already on the people.yml digest issue in this cohort's classroom-config
        # (`read_cohort_people`), with a mail beside it to the people who can act on it; a
        # red X here opens "Sync membership is failing" in the COURSE org and mails a
        # maintainer who cannot write another org's teaching team. A read that FAILED
        # still raises out of the loader and still reds the run.
        log_err(
            f"cohort people config {cohort_org}/{CONFIG_REPO}/"
            f"{COHORT_PEOPLE_PATH} is absent - refusing to reconcile instructors (an "
            f"absent config would prune every instructor); skipping"
        )
        return 0
    desired = _desired_for(faculty, INSTRUCTORS_TEAM, date.today().isoformat())
    errors = reconcile_team_members(
        cohort_org, INSTRUCTORS_TEAM, desired, prune=True, dry_run=dry_run
    )

    tag = term_tag(cohort_org)
    if tag is None:
        return errors
    team = f"{INSTRUCTORS_TEAM}-{tag}"
    if not dry_run:
        if not create_team(
            course_org, team, f"Instructors for {tag} (cohort-declared)"
        ):
            # The team could not be created; granting it repo access and reconciling its
            # membership would all fail against a non-existent team (triple-counting the
            # one root failure and firing doomed API calls). Report it once and stop here.
            return errors + 1
        for repo in _tag_repos(content_repos, assignments, tag):
            if not grant_team_repo_access(course_org, team, repo, "push"):
                errors += 1
    errors += reconcile_team_members(
        course_org, team, desired, prune=True, dry_run=dry_run
    )
    return errors


def sync(
    course_org: str, cohorts: list[str] | None = None, dry_run: bool = False
) -> int:
    """Reconcile course_admins (course org + `cohorts`, every registered cohort if
    not given) and, for each of those same cohorts, that cohort's own
    instructors/TAs (its own team + its course-org tag team). Pass an explicit
    single-item list to scope to just one cohort, e.g. a freshly bootstrapped one,
    without re-touching every other cohort."""
    targets = discover_cohorts(course_org) if cohorts is None else cohorts
    log_step(
        f"Materialising faculty access: course-admin across {1 + len(targets)} "
        f"org(s), instructors across {len(targets)} cohort(s)"
    )
    errors = sync_course_admins(course_org, targets, dry_run=dry_run)
    # Fetched once, not once per cohort - discover_content_repos/discover_assignments
    # depend only on course_org, not on which cohort is being processed.
    content_repos = discover_content_repos(course_org)
    assignments = discover_assignments(course_org)
    for cohort_org in targets:
        errors += sync_cohort_instructors(
            course_org, cohort_org, content_repos, assignments, dry_run=dry_run
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    errors = sync(args.course_org, dry_run=args.dry_run)
    if errors:
        log_err(f"{errors} errors during sync")
        return 1
    log_ok("Sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
