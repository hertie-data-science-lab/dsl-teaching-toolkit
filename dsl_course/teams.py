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

import csv
import io

from .course import CONFIG_REPO
from .gh_contents import get_file_content, require_csv_header, strip_bom

TEAMS_PATH = "teams.csv"
FIELDS = ("assignment", "team", "github_handle")


def parse(text: str) -> dict[str, dict[str, list[str]]]:
    """Parse teams.csv into {assignment: {team: [handles]}}.

    Blank rows are skipped; a handle listed twice in a team is de-duplicated; member
    order follows first appearance so provisioning is deterministic.

    Assignment keys and team names are both CASEFOLDED. The GitHub team they materialise
    into is lower-cased (`sync_teams.team_slug`) and so is the repo named after them, so
    `Wizards` and `wizards` were always one team downstream while reading here as two -
    two entries in the parsed map, two provisioning units, one repo. The Join-team form
    already writes both lower-case; a schedule key declared `Assignment-4` (or a legacy
    hand-edited row) is what the lookups arrive with, and folding here is what makes the
    two agree - keyed raw, such an assignment found no teams at all and every group
    handout, snapshot and grading pass for it silently had nothing to do."""
    out: dict[str, dict[str, list[str]]] = {}
    reader = csv.DictReader(io.StringIO(strip_bom(text)))
    require_csv_header(reader.fieldnames, FIELDS, "teams.csv")
    for row in reader:
        assignment = (row.get("assignment") or "").strip().casefold()
        team = (row.get("team") or "").strip().casefold()
        handle = (row.get("github_handle") or "").strip()
        if not (assignment and team and handle):
            continue
        members = out.setdefault(assignment, {}).setdefault(team, [])
        if handle not in members:
            members.append(handle)
    return out


def load(cohort_org: str) -> dict[str, dict[str, list[str]]]:
    """Fetch + parse teams.csv from the cohort's PRIVATE classroom-config repo.

    A pure loader: a missing CSV returns {} silently. Whether that is benign (a
    cohort with no group assignments yet) or an error (group provisioning/grading
    asked for) is the caller's call - each contextualises it for itself."""
    content = get_file_content(cohort_org, CONFIG_REPO, TEAMS_PATH)
    return parse(content) if content is not None else {}


def teams_for(
    per: dict[str, dict[str, list[str]]], assignment: str
) -> dict[str, list[str]]:
    """The {team: [handles]} map for one assignment (empty if none).

    Casefolded on the way in, to match `parse`: the caller's key comes from schedule.yml,
    which faculty write by hand, while the form writes the lower-cased spelling."""
    return per.get(assignment.strip().casefold(), {})
