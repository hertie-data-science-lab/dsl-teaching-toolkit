"""dsl-course sync-faculty -- materialise course-admin (course-wide) and
instructors/TAs (per-semester) team membership.

Two independent flows, split by role rather than by "stability":

- `course_admins` - genuinely course-wide (course director / permanent admin, needs
  admin rights everywhere). Declared ONCE on the persistent COURSE org's
  `.github/dsl-course.yml` `people:` block - the SSOT, reconciled into the course
  org's own `course-admin` team AND mirrored into every semester org's own
  `course-admin` team. Unchanged from the original course-org-SSOT design.
- `instructors`/`teaching_assistants` - genuinely semester-scoped (most semesters have
  different lecturers/TAs). Declared PER SEMESTER, in that semester's own
  `semester-config/instructors.yml` (see `load_semester_faculty`) - reconciled into that
  semester's own `instructors` team, AND synced UP into a parallel, tag-scoped
  `instructors-<tag>` team on the COURSE org (push access on just that tag's
  content repos, PLUS the central `.github` repo so its members can also use the
  central dispatch workflows), so a semester's own people can push materials without a
  course-level declaration. No merge/union across semesters - each semester's tag gets
  its own team, so there's no "which semester wins" ambiguity and no
  accumulate-forever list.

Each person entry requires `github_handle` (the only field that grants access) and, for
an instructor or a TA, `email` (the only way a notification reaches them - see
`teaching_contacts`); `start`/`end` (optional ISO dates) bound when they're active,
giving auto-rotation with no manual removal step. Every reconcile here is FULL
(add + remove) - a lapsed `end` date or a deleted entry revokes access on the next sync,
same as an edit to students.csv/teams.csv.

A missing or malformed `email` is reported and does NOT withhold access: a semester that
has not filled it in keeps working (and keeps getting the @mention on its digest issue)
rather than losing its team on the next sync.

Usage:
    python3 -m dsl_course.sync_faculty --course-org hertie-dsl-demo-course-e1234
"""

from __future__ import annotations

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
    INSTRUCTOR_ROLES,
    INSTRUCTORS_FILE,
    INSTRUCTORS_TEAM,
    OLD_PEOPLE_FILE,
    RETIRED_COURSE_KEYS,
    active_today,
    semester_of,
)
from .discovery import (
    discover_assignments,
    discover_content_repos,
    live_semesters,
)
from .faults import NOT_MIGRATED, ConfigFault, NotMigrated, Unusable, not_migrated_fault
from .gh_contents import line_of, load_yaml_config, take_lines
from .gh_teams import (
    CREATED,
    create_team_outcome,
    is_valid_github_username,
    reconcile_team_members,
)
from .log import CLIParser, add_preview_flag, log, log_err, log_ok, log_step

ROLE_TEAM = {
    "instructors": INSTRUCTORS_TEAM,
    "teaching_assistants": INSTRUCTORS_TEAM,
    "course_admins": COURSE_ADMIN_TEAM,
}
SEMESTER_PEOPLE_PATH = INSTRUCTORS_FILE
# The roles a semester declares: its teaching team, the people a notification is addressed
# to and therefore the entries `email:` is required on. course_admins is course-level and
# notified through the course org, not a semester's instructors.yml.
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
    where: str,
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
    `repo`, with `file`, is what makes that line a place: the same block is a semester's
    `semester-config/instructors.yml` and a course org's `.github/dsl-course.yml`."""
    return ConfigFault(
        where,
        what,
        file=file,
        field=field,
        in_repo=repo,
        lineno=line_of(lines, field),
    )


def _declared(
    meta: dict, faults: list[ConfigFault] | None, file: str, repo: str
) -> dict[str, list[tuple[str, object]]] | None:
    """`{role key: [(where, entry)]}` from either shape of a people block, or None.

    `instructors.yml`'s one `instructors:` list, grouped by each entry's REQUIRED `role:`
    (an entry without a valid one is skipped and, with `faults`, reported); or a course
    file's `people:` mapping of role -> list. `where` is the entry's place in the file as
    written - its identity in the digest's state."""
    listed = meta.get("instructors")
    if isinstance(listed, list):
        out: dict[str, list[tuple[str, object]]] = {}
        for index, p in enumerate(listed):
            where = f"instructors[{index}]"
            role = INSTRUCTOR_ROLES.get(
                str(p.get("role") or "") if isinstance(p, dict) else ""
            )
            if role is None:
                lines = take_lines(p) if isinstance(p, dict) else {}
                log_err(f"  ! skipping {where}: `role:` is not one of {_ROLE_WORDS}")
                if faults is not None:
                    faults.append(
                        _people_fault(
                            where,
                            "role",
                            f"`role:` must be one of {_ROLE_WORDS} - the entry is "
                            f"skipped, so it grants no access and is not notified",
                            lines,
                            file,
                            repo,
                        )
                    )
                continue
            out.setdefault(role, []).append((where, p))
        return out
    people = meta.get("people")
    if not isinstance(people, dict):
        return None
    take_lines(people)
    return {
        role: [(f"people.{role}[{i}]", p) for i, p in enumerate(people.get(role) or [])]
        for role in ROLE_TEAM
    }


_ROLE_WORDS = " | ".join(INSTRUCTOR_ROLES)


def parse_faculty_from_meta(
    meta: dict,
    faults: list[ConfigFault] | None = None,
    file: str = SEMESTER_PEOPLE_PATH,
    repo: str = CONFIG_REPO,
) -> dict[str, list[dict]]:
    """Parse an already-loaded config mapping's `people:` block (course org's
    dsl-course.yml, or a semester's instructors.yml - same schema) for the roles in ROLE_TEAM.
    Only entries with a `github_handle` grant access; a named entry without one is a
    legitimate display-only card (noted, not an error), anything else is junk (flagged).

    An instructor/TA entry with no usable `email:` is an error line naming the role and
    the handle - the entry still grants access, it just cannot be notified.

    `faults` collects the same three things for the notifier: an entry with no handle to
    grant anything to, a handle that cannot be a GitHub username (adding it to a team
    would INVITE it, so the sync skips it), and a teaching entry no notification can
    reach. `file` and `repo` name where they are - the same schema is a semester's
    `semester-config/instructors.yml` and a course org's `.github/dsl-course.yml`, and a fault
    that cited the wrong one would link a reader at a file that does not exist.

    The line stamps (`take_lines`) are consumed here whether or not anybody asked for
    faults, so no consumer downstream can ever render the loader's reserved key."""
    faculty: dict[str, list[dict]] = {}
    declared = _declared(meta, faults, file, repo)
    if declared is None:
        return {}
    for role in ROLE_TEAM:
        entries = []
        for where, p in declared.get(role, []):
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
                            where,
                            "github_handle",
                            "this is not a valid GitHub username - the entry is "
                            "skipped, so it grants no access (adding it to a team "
                            "would invite an arbitrary account to the org)",
                            lines,
                            file,
                            repo,
                        )
                    )
                # A course admin's `email:` is OPTIONAL: when any admin declares one,
                # `mailer.course_admin_addresses` prefers them over the
                # `DSL_COURSE_ADMIN_EMAILS` org secret. Absent is fine; present and
                # unusable is a fault, because the admin who wrote it expects mail.
                if (
                    role == "course_admins"
                    and file == COURSE_CONFIG
                    and str(p.get("email") or "").strip()
                    and valid_email(p.get("email")) is None
                ):
                    log_err(
                        f"  ! course_admins entry {p['github_handle']} has an `email:` "
                        f"that is not an address - this admin is not emailed: see "
                        f"{COURSE_CONFIG}"
                    )
                    if faults is not None:
                        faults.append(
                            _people_fault(
                                where,
                                "email",
                                "this `email:` is not an address - access is still "
                                "granted, but no course mail reaches this admin",
                                lines,
                                file,
                                repo,
                            )
                        )
                # `email:` is REQUIRED on a semester's teaching entries: only a semester
                # declares people who are notified through their entry alone.
                if (
                    role in TEACHING_ROLES
                    and file == SEMESTER_PEOPLE_PATH
                    and valid_email(p.get("email")) is None
                ):
                    log_err(
                        f"  ! {role} entry {p['github_handle']} has no usable `email:` "
                        f"- it is required (access still granted, but this person is "
                        f"not notified): see {file}"
                    )
                    if faults is not None:
                        faults.append(
                            _people_fault(
                                where,
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
                            where,
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
    string), in declaration order - this semester's teaching team as it stands today.

    The role rides along because who gets COPIED on a notification depends on it: a mail
    addressed to a TA copies the instructors. Reading it back off the entry afterwards
    would mean iterating instructors.yml a second way."""
    return [
        (role, p)
        for role in TEACHING_ROLES
        for p in faculty.get(role) or []
        if active_today(p.get("start"), p.get("end"), today)
    ]


class Contact(NamedTuple):
    """One reachable member of a semester's teaching team.

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
    instructors.yml once and both answers are about the same tick. Taking the raw `people:`
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


def course_admin_emails(faculty: dict[str, list[dict]], today: str) -> list[str]:
    """The usable `email:` of every course admin active on `today`, in declaration order.

    What `mailer.course_admin_addresses` prefers over the org secret. Never log the
    return value - an address is personal data; log a count."""
    return [
        email
        for p in faculty.get("course_admins") or []
        if active_today(p.get("start"), p.get("end"), today)
        and (email := valid_email(p.get("email")))
    ]


def without_email(faculty: dict[str, list[dict]], today: str) -> list[str]:
    """The handles of active instructors/TAs no notification can reach. Handles, which
    are public and loggable; the addresses themselves never leave this module."""
    return [
        str(p["github_handle"])
        for _role, p in _active_teaching_entries(faculty, today)
        if valid_email(p.get("email")) is None
    ]


def _semester_roles_only(faculty: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """A semester's instructors.yml declares instructors/TAs only - course_admins stays
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
    dispatch workflows (`.github` is cross-semester infrastructure, not itself
    tag-scoped)."""
    matching = [r for r in content_repos if _matches_tag(r, tag)] + [
        r for r in assignments if _matches_tag(r, tag)
    ]
    return [".github"] + matching


# ---------------------------------------------------------------------- gh/git wiring


def load_faculty(course_org: str) -> dict[str, list[dict]] | None:
    """Fetch + parse the course org's `.github/dsl-course.yml` `people:` block -
    course_admins only in practice; instructors/TAs are declared per semester
    (see `load_semester_faculty`), but any stray entries here are still parsed
    (and reconciled) the same way `parse_faculty_from_meta` always has.

    Returns None when dsl-course.yml is genuinely ABSENT - the caller must then NOT prune
    (an absent config is not an empty desired set). A present-but-empty `people:` block
    parses to {} and legitimately empties the team."""
    meta = load_yaml_config(course_org, ".github", "dsl-course.yml")
    if meta is None:
        return None
    return parse_faculty_from_meta(meta)


@cache
def load_semester_faculty(semester_org: str) -> dict[str, list[dict]] | None:
    """Fetch + parse this semester's own semester-config/instructors.yml - instructors/TAs
    only (no course_admins key here; that role stays exclusively course-level).

    Returns None when instructors.yml is genuinely ABSENT (do not prune). A file with no
    `instructors:` list raises `NoInstructorsList`, and `instructors: []` - said in as many
    words - is the one way to empty the team.

    THE door to a semester's instructors.yml, and memoised per process like `repos._repo`: a
    release tick asks it who to notify, `status` asks it who is unreachable, and the file
    changes only when somebody edits it. The memo also means the "no usable `email:`"
    error lines are printed once per run rather than once per reader.
    `tests/conftest.py` clears it."""
    meta, path = _load_semester_file(semester_org)
    if meta is None:
        return None
    return _semester_roles_only(parse_faculty_from_meta(meta, file=path))


class NoInstructorsList(Unusable):
    """A present `instructors.yml` that declares no `instructors:` list - the seeded,
    all-comment skeleton, or a file emptied by hand. Refused rather than read as "nobody":
    a sweep with prune on an empty desired set would strip every instructor's access."""


def _load_semester_file(
    semester_org: str, *, lines: bool = False
) -> tuple[dict | None, str]:
    """`(meta, path)` for this semester's `instructors.yml`; `(None, path)` when it is
    absent. A semester that has only the old `people.yml` is refused as NOT_MIGRATED:
    read as absent, it would keep nobody's access and tell nobody why. Raises exactly what
    `load_yaml_config` raises otherwise."""
    meta = (
        load_yaml_config(semester_org, CONFIG_REPO, SEMESTER_PEOPLE_PATH, lines=True)
        if lines
        else load_yaml_config(semester_org, CONFIG_REPO, SEMESTER_PEOPLE_PATH)
    )
    listed = isinstance(meta, dict) and isinstance(meta.get("instructors"), list)
    # The old file beside a new one that lists nobody - absent, or the seeded all-comment
    # skeleton - is a semester that has not migrated, whatever the new file says.
    if not listed and load_yaml_config(semester_org, CONFIG_REPO, OLD_PEOPLE_FILE):
        raise NotMigrated(
            OLD_PEOPLE_FILE, INSTRUCTORS_FILE, f"{semester_org}/{CONFIG_REPO}"
        )
    # The old file's shape under the new name: refused too, never read as "nobody".
    if isinstance(meta, dict) and "people" in meta and not listed:
        raise NotMigrated("people:", "instructors:", SEMESTER_PEOPLE_PATH)
    if meta is not None and not listed:
        raise NoInstructorsList(
            f"{semester_org}/{CONFIG_REPO}/{SEMESTER_PEOPLE_PATH} declares no "
            f"`instructors:` list - nothing is reconciled from it"
        )
    return meta, SEMESTER_PEOPLE_PATH


def read_semester_people(
    semester_org: str, faults: list[ConfigFault]
) -> dict[str, list[dict]] | None:
    """This semester's instructors.yml, parsed, with everything a human must fix collected.

    The fault-collecting twin of `load_semester_faculty`, and deliberately NOT memoised: it
    reads the file with line stamps (which the cached loader must not hand to the site
    renderer) and it is asked once per tick.

    A file that is ABSENT or that does not parse is a fault of its own rather than an
    exception, because both mean the same thing to a semester - nobody is granted access and
    nobody is notified - and neither is anything a release run should stop for. A read
    that FAILED (a rate limit, a token that lost its scope) still raises: "we could not
    look" must never be reported to faculty as "your file is broken"."""
    try:
        meta, path = _load_semester_file(semester_org, lines=True)
    except NotMigrated as exc:
        # The old file, or its old shape in the new one: filed against whichever holds it.
        file = OLD_PEOPLE_FILE if exc.old == OLD_PEOPLE_FILE else SEMESTER_PEOPLE_PATH
        faults.append(not_migrated_fault(exc.old, exc.new, where=file, file=file))
        return None
    except NoInstructorsList:
        faults.append(
            _file_fault(
                "this file declares no `instructors:` list yet, so nothing is reconciled "
                "from it"
            )
        )
        return None
    except yaml.YAMLError:
        faults.append(_file_fault("this file is not valid YAML, so none of it is read"))
        return None
    except Unusable:
        # `Unusable` and NOT `RuntimeError`: `load_yaml_config` raises the first for a top
        # level that is not a mapping, and `get_file_content` under it raises the second
        # for any read that was not a 404 - a rate limit, a token that lost its scope. Read
        # as the same thing, a rate limit came back as ONE fault saying instructors.yml is
        # broken, which closes every real fault in that issue as cleared and mails the
        # teaching team about it. That is the line this whole function exists to draw.
        faults.append(
            _file_fault("this file is not a YAML mapping, so none of it is read")
        )
        return None
    if meta is None:
        faults.append(
            _file_fault(
                "this file is missing, so the semester has no declared instructors"
            )
        )
        return None
    return _semester_roles_only(parse_faculty_from_meta(meta, faults, file=path))


def _file_fault(what: str) -> ConfigFault:
    """instructors.yml as a whole, unusable - no entry to name and no line to point at."""
    return ConfigFault(
        SEMESTER_PEOPLE_PATH,
        f"{what} - no instructor or TA is granted access or notified",
        file=SEMESTER_PEOPLE_PATH,
        field="people",
        fix_text=(
            f"restore {SEMESTER_PEOPLE_PATH} from the template and declare the semester's "
            f"instructors and teaching assistants in it"
        ),
    )


# What to do about a `dsl-course.yml` that is missing, is not YAML, or is not a mapping.
# The file's own fallback sentence (`faults.FIX`) says "correct the line above", which is
# the right instruction for the one fault here that HAS a line (`central_ref:`) and no
# instruction at all for the three that do not - there is no line above, and the citation
# beside it is a bare filename with nothing to link to.
_COURSE_FILE_FIX = (
    f"restore {COURSE_CONFIG} in the course org's `.github` from the template and "
    f"declare the course's admins and its `central_ref:` in it"
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
        fix_text="" if lineno else _COURSE_FILE_FIX,
    )


def read_course_config(
    course_org: str, faults: list[ConfigFault]
) -> dict[str, list[dict]] | None:
    """The COURSE org's `.github/dsl-course.yml`, parsed, with everything a human must fix
    collected. None when the file could not be used at all.

    The fault-collecting twin of `load_faculty`, and deliberately not memoised for the
    same reason `read_semester_people` is not: it reads the file with line stamps and it is
    asked once per tick.

    Three things in this file stop the whole course being reconciled, and all three come
    back as faults rather than as exceptions - an absent or unparseable file, an admin
    handle no team can be given, and a `central_ref:` no workflow can be pinned to. A read
    that FAILED still raises, because "we could not look" must never be reported to a
    course admin as "your file is broken"."""
    # `load_yaml_config` draws all three lines this function needs, and says which file
    # and what it got as it does: None for an absent file, `yaml.YAMLError` for one that
    # does not parse, `Unusable` for a top level that is not a mapping - and a read that
    # FAILED still comes out as a bare RuntimeError, which is the distinction this whole
    # function is about. `read_semester_people` reads its file exactly this way.
    try:
        meta = load_yaml_config(course_org, ".github", COURSE_CONFIG, lines=True)
    except yaml.YAMLError:
        faults.append(
            _course_fault("this file is not valid YAML, so none of it is read")
        )
        return None
    except Unusable:
        faults.append(
            _course_fault("this file is not a YAML mapping, so none of it is read")
        )
        return None
    if meta is None:
        faults.append(
            _course_fault(
                "this file is missing, so the course declares no admins and no tier"
            )
        )
        return None
    # Taken before the people block is parsed, so the top-level keys' lines are in hand
    # for `central_ref:` - and so the loader's reserved key cannot survive into anything
    # that renders this mapping.
    lines = take_lines(meta)
    for key in RETIRED_COURSE_KEYS:
        if key in meta:
            faults.append(
                ConfigFault(
                    COURSE_CONFIG,
                    f"`{key}:` is no longer read (decision 0009)",
                    field=key,
                    file=COURSE_CONFIG,
                    in_repo=".github",
                    lineno=line_of(lines, key),
                    fix_text="run the migration, which removes it",
                    code=NOT_MIGRATED,
                )
            )
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
    course_org: str, semesters: list[str], dry_run: bool = False
) -> int:
    """course_admins: declared once on the course org, mirrored unchanged into the
    course org itself and every semester's own course-admin team."""
    faculty = load_faculty(course_org)
    if faculty is None:
        # ABSENT dsl-course.yml: parsing it as "" would yield an empty desired set and a
        # pruning reconcile would strip course-admin from EVERY org. Refuse to prune.
        log_err(
            f"course identity {course_org}/.github/dsl-course.yml is absent - refusing "
            f"to reconcile course-admin (an absent config would prune every admin from "
            f"{1 + len(semesters)} org(s)); skipping"
        )
        return 1
    desired = _desired_for(faculty, COURSE_ADMIN_TEAM, date.today().isoformat())
    errors = 0
    for org in [course_org] + semesters:
        errors += reconcile_team_members(
            org, COURSE_ADMIN_TEAM, desired, prune=True, dry_run=dry_run
        )
    return errors


def sync_semester_instructors(
    course_org: str,
    semester_org: str,
    content_repos: list[str],
    assignments: list[str],
    dry_run: bool = False,
) -> int:
    """instructors/TAs: declared in this semester's own semester-config/instructors.yml,
    reconciled into that semester's own `instructors` team AND a parallel, tag-scoped
    `instructors-<tag>` team on the course org - no merge with any other semester.
    `content_repos`/`assignments` are the course org's discovered repos, passed in
    (rather than re-discovered here) so a multi-semester `sync()` fetches them once,
    not once per semester."""
    try:
        faculty = load_semester_faculty(semester_org)
    except (NotMigrated, NoInstructorsList) as exc:
        # Fail CLOSED: an old or empty file is no desired set to prune to. Skip this
        # semester's instructors sweep - the digest issue carries the fault - and stay green.
        log_err(f"  ! {exc} - instructors not reconciled for {semester_org}; skipping")
        return 0
    if faculty is None:
        # ABSENT instructors.yml: reconciling an empty desired set with prune=True would strip
        # this semester's instructors team (and its course-org tag team). Refuse to prune -
        # and stay GREEN, because a file faculty have to write is a CONTENT fault. It is
        # already on the instructors.yml digest issue in this semester's semester-config
        # (`read_semester_people`), with a mail beside it to the people who can act on it; a
        # red X here opens "Sync membership is failing" in the COURSE org and mails a
        # maintainer who cannot write another org's teaching team. A read that FAILED
        # still raises out of the loader and still reds the run.
        log_err(
            f"semester people config {semester_org}/{CONFIG_REPO}/"
            f"{SEMESTER_PEOPLE_PATH} is absent - refusing to reconcile instructors (an "
            f"absent config would prune every instructor); skipping"
        )
        return 0
    desired = _desired_for(faculty, INSTRUCTORS_TEAM, date.today().isoformat())
    errors = reconcile_team_members(
        semester_org, INSTRUCTORS_TEAM, desired, prune=True, dry_run=dry_run
    )

    tag = semester_of(semester_org)
    if tag is None:
        return errors
    team = f"{INSTRUCTORS_TEAM}-{tag}"
    outcome = None
    if not dry_run:
        outcome = create_team_outcome(
            course_org, team, f"Instructors for {tag} (semester-declared)"
        )
        if outcome is None:
            # The team could not be created; granting it repo access and reconciling its
            # membership would all fail against a non-existent team (triple-counting the
            # one root failure and firing doomed API calls). Report it once and stop here.
            return errors + 1
        for repo in _tag_repos(content_repos, assignments, tag):
            if not grant_team_repo_access(course_org, team, repo, "push"):
                errors += 1
    # A team made a moment ago is not read back: GitHub's REST API 404s a new team for up
    # to minutes, which would spend the whole lag budget and abort the reconcile.
    errors += reconcile_team_members(
        course_org,
        team,
        desired,
        prune=True,
        dry_run=dry_run,
        just_created=outcome == CREATED,
    )
    return errors


def sync(
    course_org: str, semesters: list[str] | None = None, dry_run: bool = False
) -> int:
    """Reconcile course_admins (course org + `semesters`, every registered semester if
    not given) and, for each of those same semesters, that semester's own
    instructors/TAs (its own team + its course-org tag team). Pass an explicit
    single-item list to scope to just one semester, e.g. a freshly bootstrapped one,
    without re-touching every other semester."""
    # LIVE semesters, not every registered one: a semester that has been closed out is a
    # read-only org, and every grant below would 403 on it nightly for the rest of the
    # course's life (`discovery.semester_is_live`).
    targets = live_semesters(course_org) if semesters is None else semesters
    log_step(
        f"Materialising faculty access: course-admin across {1 + len(targets)} "
        f"org(s), instructors across {len(targets)} semester(s)"
    )
    errors = sync_course_admins(course_org, targets, dry_run=dry_run)
    # Fetched once, not once per semester - discover_content_repos/discover_assignments
    # depend only on course_org, not on which semester is being processed.
    content_repos = discover_content_repos(course_org)
    assignments = discover_assignments(course_org)
    for semester_org in targets:
        errors += sync_semester_instructors(
            course_org, semester_org, content_repos, assignments, dry_run=dry_run
        )
    return errors


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument("--course-org", required=True)
    add_preview_flag(parser, "Report the team changes; make none (default).")
    args = parser.parse_args()

    errors = sync(args.course_org, dry_run=args.preview)
    if errors:
        log_err(f"{errors} errors during sync")
        return 1
    log_ok("Sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
