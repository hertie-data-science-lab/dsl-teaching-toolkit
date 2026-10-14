"""dsl-course teams -- per-assignment group membership, read from students.csv.

A team assignment has a `team:<assignment number>` column in the semester's private
`students.csv` (decision 0039 rule 4); a team is the rows sharing a cell value, blank = no
team:

    email,name,role,team:4
    anna@example.org,Anna Adams,,team-x
    ben@example.org,Ben Baker,,team-x
    carla@example.org,Carla Cohen,,team-y

Students self-select by opening a "Join team" issue in `join` (the workflow writes their
cell - authenticated author, size-capped); faculty type a cell directly, before anyone has
joined if they like. `sync_teams` then materialises a GitHub Team `<assignment>-<team>`
from the onboarded members (one-way, idempotent), and group-assignment provisioning grants
that team its shared repo. The Team is a downstream projection of the file, never
authoritative, so it cannot drift the way a Classroom-managed team does.
"""

from __future__ import annotations

from . import roster, schedule
from .course import INSTRUCTORS_TEAM, ROLE_TEAMS
from .faults import ConfigFault

# Team slugs students may never materialise. A team cell is STUDENT-written (the public
# Join-team issue form), and `team_slug("course", "admin")` is `course-admin` - the faculty
# team that holds admin on every repo in the semester. Reconciling that slug would add the
# student to it and prune the real admins. The workflow refuses these at the form; this is
# the backstop for a cell that reached the file any other way.
RESERVED_TEAM_SLUGS = ROLE_TEAMS


def team_slug(assignment: str, team: str) -> str:
    """The GitHub Team name/slug materialised for one (assignment, team) pair.

    Assignment-prefixed so a team name reused across assignments (e.g. `wizards` in two
    projects) maps to distinct org-unique teams. Lower-cased to match the slug GitHub
    derives from the team name."""
    return f"{assignment}-{team}".lower()


def is_reserved_slug(slug: str) -> bool:
    return slug in RESERVED_TEAM_SLUGS or slug.startswith(f"{INSTRUCTORS_TEAM}-")


def columns(sched: schedule.Schedule) -> dict[str, str]:
    """`{schedule key: its team column suffix}`: the assignment's number (`team:4`), or
    its key when the entry has none."""
    return {
        key: str(entry.number) if entry.number is not None else key
        for key, entry in sched.assignments.items()
    }


def parse(
    students: list[roster.Student],
    keys: dict[str, str],
    faults: list[ConfigFault] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """`{assignment key: {team: [handles]}}` from the roster's team cells. `keys` is
    `columns(schedule)`.

    Keys and team names are CASEFOLDED: the GitHub team they materialise into is
    lower-cased, so `Wizards` and `wizards` are one team. Only ONBOARDED students are
    members - a faculty-assigned cell typed before its student has joined waits for the
    handle. Member order follows the file, so provisioning is deterministic.

    `faults` collects what this parse acts on differently from how it reads: a cell naming
    a faculty team (ignored), and a `team:` column that is no assignment in the schedule
    (nobody in it is grouped)."""
    by_suffix = {suffix.casefold(): key.casefold() for key, suffix in keys.items()}
    out: dict[str, dict[str, list[str]]] = {}
    unknown: set[str] = set()
    for s in students:
        for suffix, team in s.teams.items():
            key = by_suffix.get(suffix.casefold())
            if key is None:
                unknown.add(suffix)
                continue
            if is_reserved_slug(team_slug(key, team)):
                if faults is not None:
                    faults.append(
                        ConfigFault(
                            roster.team_column(suffix),
                            "a cell in this column names a FACULTY team - the toolkit "
                            "will not manage one from students.csv, so the cell is "
                            "ignored",
                            file=roster.STUDENTS_PATH,
                            field=roster.team_column(suffix),
                        )
                    )
                continue
            if not s.onboarded:
                continue
            members = out.setdefault(key, {}).setdefault(team, [])
            handle = s.github_handle.strip().casefold()
            if handle not in members:
                members.append(handle)
    if faults is not None:
        for suffix in sorted(unknown):
            faults.append(
                ConfigFault(
                    roster.team_column(suffix),
                    "this column names no assignment in schedule.yml, so nobody in it "
                    "is put in a team",
                    file=roster.STUDENTS_PATH,
                    field=roster.team_column(suffix),
                    fix_text=(
                        "name the column `team:<number>` after an assignment's number in "
                        "schedule.yml"
                    ),
                )
            )
    return out


def load(
    semester_org: str, faults: list[ConfigFault] | None = None
) -> dict[str, dict[str, list[str]]]:
    """Every assignment's teams, from the semester's roster and schedule. `{}` when the
    roster is absent - whether that is benign or an error is the caller's call.

    `faults` is `parse`'s, for the caller that is checking the file rather than reading
    it."""
    return load_or_none(semester_org, faults) or {}


def load_or_none(
    semester_org: str, faults: list[ConfigFault] | None = None
) -> dict[str, dict[str, list[str]]] | None:
    """`load`, but None when students.csv is ABSENT (a 404) - for the caller that would
    take access away on the strength of an empty answer (the pruning team sync)."""
    students = roster.load(semester_org)
    if students is None:
        return None
    return parse(students, columns(schedule.load(semester_org)), faults)


def teams_for(
    per: dict[str, dict[str, list[str]]], assignment: str
) -> dict[str, list[str]]:
    """The {team: [handles]} map for one assignment (empty if none).

    Casefolded on the way in, to match `parse`: the caller's key comes from schedule.yml,
    which faculty write by hand, while the form writes the lower-cased spelling."""
    return per.get(assignment.strip().casefold(), {})
