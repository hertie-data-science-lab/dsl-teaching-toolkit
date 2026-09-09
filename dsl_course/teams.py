"""dsl-course teams -- per-assignment group membership from classroom-config/teams.csv.

`teams.csv` (private, in the cohort's `classroom-config` repo) is the single source of
truth for who is in which team for which assignment:

    assignment,team,github_handle
    assignment-4-project,team-x,anna-adams
    assignment-4-project,team-x,ben-baker
    assignment-4-project,team-y,carla-cohen

Students self-select by opening a "Join team" issue in `welcome` (the workflow appends a
row - authenticated author, size-capped); faculty & instructors override by editing the CSV directly. This
CSV is the only writer surface for membership. `sync_teams` then materialises a GitHub Team
`<assignment>-<team>` from it (one-way, idempotent), and group-assignment provisioning grants
that team its shared repo. Because the Team is a downstream projection of the CSV - never
authoritative - it can't drift out of sync the way a Classroom-managed team does.
"""

from __future__ import annotations

from functools import cache

from .course import CONFIG_REPO, INSTRUCTORS_TEAM, ROLE_TEAMS
from .faults import ConfigFault, csv_row
from .gh_contents import get_file_content, read_csv

TEAMS_PATH = "teams.csv"
FIELDS = ("assignment", "team", "github_handle")

# Team slugs students may never materialise. teams.csv is STUDENT-written (the public
# Join-team issue form), and `team_slug("course", "admin")` is `course-admin` - the faculty
# team that holds admin on every repo in the cohort. Reconciling that slug from teams.csv
# would add the student to it and prune the real admins. The workflow refuses these at the
# form; this is the backstop for a row that reached the CSV any other way.
RESERVED_TEAM_SLUGS = ROLE_TEAMS


def team_slug(assignment: str, team: str) -> str:
    """The GitHub Team name/slug materialised for one (assignment, team) pair.

    Assignment-prefixed so a team name reused across assignments (e.g. `wizards` in two
    projects) maps to distinct org-unique teams. Lower-cased to match the slug GitHub
    derives from the team name."""
    return f"{assignment}-{team}".lower()


def is_reserved_slug(slug: str) -> bool:
    return slug in RESERVED_TEAM_SLUGS or slug.startswith(f"{INSTRUCTORS_TEAM}-")


def _row_fault(lineno: int, field: str, what: str) -> ConfigFault:
    """One teams.csv row the toolkit will not act on.

    ROW AND COLUMN ONLY - never the handle and never the team name. This file is written
    by students through a public issue form, and the fault text reaches a mail, an issue
    and a run log."""
    return ConfigFault(
        csv_row(lineno),
        what,
        file=TEAMS_PATH,
        field=field,
        lineno=lineno,
        fix_text=(
            f"fix row {lineno} of {TEAMS_PATH}; handles must be onboarded roster handles"
        ),
    )


def parse(
    text: str,
    faults: list[ConfigFault] | None = None,
    known_handles: set[str] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Parse teams.csv into {assignment: {team: [handles]}}.

    Blank rows are skipped; a handle listed twice in a team is de-duplicated, CASEFOLDED
    (GitHub logins are case-insensitive, so `ALICE` and `alice` are one account and were
    two members here - two collaborator adds, two grade rows); member order follows first
    appearance so provisioning is deterministic.

    Assignment keys and team names are both CASEFOLDED. The GitHub team they materialise
    into is lower-cased (`sync_teams.team_slug`) and so is the repo named after them, so
    `Wizards` and `wizards` were always one team downstream while reading here as two -
    two entries in the parsed map, two provisioning units, one repo. The Join-team form
    already writes both lower-case; a schedule key declared `Assignment-4` (or a legacy
    hand-edited row) is what the lookups arrive with, and folding here is what makes the
    two agree - keyed raw, such an assignment found no teams at all and every group
    handout, snapshot and grading pass for it silently had nothing to do.

    `faults` collects the rows this parse acts on differently from how they read - a
    faculty team named where a project team belongs, a student claimed by two teams of one
    assignment, a row de-duplicated away - each of which was a silent drop or a `log_err`
    in a sync nobody watches.

    `known_handles` is the onboarded roster, when the caller has it: a handle that is not
    on it would INVITE an arbitrary GitHub account into a private org, so the syncs refuse
    it, and until now the only trace was a verbose-only line. None means "not checked" and
    is not the same as an empty set - an unreadable roster must not report every row as a
    stranger."""
    out: dict[str, dict[str, list[str]]] = {}
    claimed: dict[tuple[str, str], tuple[str, int]] = {}  # (assignment, handle) -> team
    # Folded here, not by the caller: GitHub logins are case-insensitive and the rows are
    # folded on the way in, so an allowlist in the roster's own casing would read every
    # differently-typed handle as a stranger.
    allowed = None if known_handles is None else {h.casefold() for h in known_handles}
    reader = read_csv(text, FIELDS, TEAMS_PATH, faults)
    for row in reader:
        lineno = reader.line_num
        assignment = (row.get("assignment") or "").strip().casefold()
        team = (row.get("team") or "").strip().casefold()
        handle = (row.get("github_handle") or "").strip().casefold()
        if not (assignment and team and handle):
            continue
        if faults is not None:
            faults += _row_faults(lineno, assignment, team, handle, claimed, allowed)
        claimed.setdefault((assignment, handle), (team, lineno))
        members = out.setdefault(assignment, {}).setdefault(team, [])
        if handle not in members:
            members.append(handle)
    return out


def _row_faults(
    lineno: int,
    assignment: str,
    team: str,
    handle: str,
    claimed: dict[tuple[str, str], tuple[str, int]],
    known_handles: set[str] | None,
) -> list[ConfigFault]:
    """Everything wrong with one row, in the order a reader would notice it."""
    found = []
    slug = team_slug(assignment, team)
    if is_reserved_slug(slug):
        found.append(
            _row_fault(
                lineno,
                "team",
                "this row names a FACULTY team - the toolkit will not manage one from "
                "teams.csv, so the row is ignored",
            )
        )
    was = claimed.get((assignment, handle))
    if was and was[0] != team:
        found.append(
            _row_fault(
                lineno,
                "team",
                f"this handle is already in another team for the same assignment "
                f"(row {was[1]}) - two teams cannot claim one student",
            )
        )
    elif was:
        found.append(
            _row_fault(
                lineno,
                "github_handle",
                f"this handle is already listed for this team on row {was[1]} - the "
                f"duplicate row is ignored",
            )
        )
    if known_handles is not None and handle not in known_handles:
        found.append(
            _row_fault(
                lineno,
                "github_handle",
                "this handle is not an onboarded roster handle - it is NOT added to the "
                "team (adding it would invite an arbitrary GitHub account to the org)",
            )
        )
    return found


@cache
def _teams_text(cohort_org: str) -> str | None:
    """teams.csv's text, read ONCE per cohort per process.

    A single run asks for it repeatedly - the handout, the collection and the off-boarding
    revoke each want the same file - and only the welcome workflow writes it, in a process
    of its own. The TEXT is memoised rather than `load`'s map, so each caller still parses
    its own copy and cannot mutate another's. Cleared between tests (tests/conftest.py)."""
    return get_file_content(cohort_org, CONFIG_REPO, TEAMS_PATH)


def load(
    cohort_org: str,
    faults: list[ConfigFault] | None = None,
    known_handles: set[str] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Fetch + parse teams.csv from the cohort's PRIVATE classroom-config repo.

    A pure loader: a missing CSV returns {} silently. Whether that is benign (a
    cohort with no group assignments yet) or an error (group provisioning/grading
    asked for) is the caller's call - each contextualises it for itself.

    `faults` and `known_handles` are `parse`'s, for the caller that is checking the file
    rather than reading it."""
    content = _teams_text(cohort_org)
    return parse(content, faults, known_handles) if content is not None else {}


def teams_for(
    per: dict[str, dict[str, list[str]]], assignment: str
) -> dict[str, list[str]]:
    """The {team: [handles]} map for one assignment (empty if none).

    Casefolded on the way in, to match `parse`: the caller's key comes from schedule.yml,
    which faculty write by hand, while the form writes the lower-cased spelling."""
    return per.get(assignment.strip().casefold(), {})
