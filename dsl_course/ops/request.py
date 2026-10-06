"""Parse and authorise a `dsl.request/1` - the one string the Console workflow is handed.

The request is user text arriving through a public workflow's input, so it is validated
here in full before anything runs: its own shape, then the op's `args_schema`, then who
is asking (`schema_check.validate`, the package's small JSON Schema subset).
"""

from __future__ import annotations

import json

from ..course import COURSE_ADMIN_TEAM, INSTRUCTORS_TEAM
from ..discovery import discover_semesters
from ..faults import NOT_MIGRATED, moved_text, not_migrated_text
from ..gh_teams import get_team_members, list_teams
from ..schema_check import validate
from .registry import (
    BOOTSTRAP_OP,
    COURSE,
    ORG_PATTERN,
    REGISTRY,
    REQUEST_SCHEMA,
    SEMESTER,
    Request,
)

# The request's own shape, as JSON Schema. `op` is an enum of the registry, so the schema
# export and the parser cannot disagree about which ops exist.
REQUEST_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "schema": {"type": "string", "enum": [REQUEST_SCHEMA]},
        "op": {"type": "string", "enum": sorted(REGISTRY)},
        "actor": {"type": "string", "pattern": ORG_PATTERN},
        "course_org": {"type": "string", "pattern": ORG_PATTERN},
        "semester_org": {"type": "string", "pattern": ORG_PATTERN},
        "args": {"type": "object"},
        "preview": {"type": "boolean"},
        "client": {"type": "string", "pattern": r"^[A-Za-z0-9._/+-]{0,64}$"},
    },
    "required": ["schema", "op", "actor", "course_org", "args", "preview"],
    "additionalProperties": False,
}

# Old request spellings (decision 0012), old -> new. Nothing reads them: a request that
# carries one is refused as NOT_MIGRATED before anything else is checked, naming the new
# spelling, so a console that has not caught up says so instead of failing as a typo.
RENAMED_REQUEST_FIELDS = {"cohort_org": "semester_org"}
RENAMED_REQUEST_ARGS = {
    "cohort_dest_repo": "semester_dest_repo",
    "cohort_dest_path": "semester_dest_path",
    "tag": "semester",
    "format": "formats",
    "include_solution": "solution_datetime",
}
# Args that went (decision 0009), with where the fact lives now: an older console build
# sending one gets a sentence, not a schema error.
MOVED_REQUEST_ARGS = {
    "slug": "the schedule names the entry; a template two entries share is refused",
    "team_formation": "it is set per semester in semester-config/assignments.yml",
    "visibility": "it is set per semester in semester-config/assignments.yml",
}


def _refuse_old_spellings(
    fields: object, renames: dict[str, str], where: str, say=not_migrated_text
) -> None:
    for old, new in renames.items():
        if isinstance(fields, dict) and old in fields:
            raise RequestError(NOT_MIGRATED, f"{where}.{old}: {say(old, new)}.")


class RequestError(ValueError):
    """A request the engine refuses before running anything. `code` is the outcome's
    reason code; the message is the sentence that goes with it."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


def parse_request(text: str) -> Request:
    """A `Request` from the workflow's input, or `RequestError` saying what is wrong."""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise RequestError("BAD_REQUEST", "The request is not valid JSON.") from None
    _refuse_old_spellings(raw, RENAMED_REQUEST_FIELDS, "$")
    _refuse_old_spellings(
        raw.get("args") if isinstance(raw, dict) else None,
        RENAMED_REQUEST_ARGS,
        "$.args",
    )
    _refuse_old_spellings(
        raw.get("args") if isinstance(raw, dict) else None,
        MOVED_REQUEST_ARGS,
        "$.args",
        moved_text,
    )
    problems = validate(raw, REQUEST_JSON_SCHEMA)
    if problems:
        code = (
            "UNKNOWN_OP"
            if any(p.startswith("$.op:") for p in problems)
            else "BAD_REQUEST"
        )
        raise RequestError(code, f"The request is malformed: {'; '.join(problems)}.")
    op = REGISTRY[raw["op"]]
    problems = validate(raw["args"], op.args_schema, "$.args")
    if problems:
        raise RequestError(
            "BAD_ARGS", f"{op.name} was asked for with {'; '.join(problems)}."
        )
    if op.scope == SEMESTER and not raw.get("semester_org"):
        raise RequestError("BAD_REQUEST", f"{op.name} needs a semester_org.")
    if op.scope == COURSE and raw.get("semester_org"):
        raise RequestError(
            "BAD_REQUEST", f"{op.name} is course-wide and takes no semester_org."
        )
    if raw["preview"] and op.preview_flag is None:
        raise RequestError(
            "NO_PREVIEW", f"{op.name} has no preview; ask for it without one."
        )
    return Request(
        op=op.name,
        actor=raw["actor"],
        course_org=raw["course_org"],
        semester_org=raw.get("semester_org"),
        args=raw["args"],
        preview=raw["preview"],
        client=raw.get("client", ""),
    )


def _member(org: str, team: str, actor: str) -> bool | None:
    members = get_team_members(org, team)
    if members is None:
        return None
    return actor.casefold() in {m.casefold() for m in members}


def check_access(request: Request) -> str | None:
    """None when `request.actor` may run the op, else the sentence saying why not.

    The semester must be registered under the course. Course admins may run everything. Otherwise an op whose `required_team` is the
    instructors team needs the actor in the SEMESTER's instructors team, or - for a
    course-wide op - in one of the course org's `instructors-<term>` teams, which is where
    Sync membership mirrors every semester's teaching team. A team that cannot be read counts
    as "not a member": this gate fails closed."""
    op = REGISTRY[request.op]
    actor = request.actor
    # The semester must be this course's: a course admin here is nobody in another course.
    # Bootstrap is the one op whose semester is not registered yet - it registers it.
    if op.scope == SEMESTER and op.name != BOOTSTRAP_OP:
        registered = {c.casefold() for c in discover_semesters(request.course_org)}
        if request.semester_org.casefold() not in registered:
            return f"{request.semester_org} is not a semester of {request.course_org}."
    if _member(request.course_org, COURSE_ADMIN_TEAM, actor):
        return None
    refusal = f"@{actor} may not run {op.name}: it needs the {op.required_team} team"
    if op.required_team != INSTRUCTORS_TEAM:
        return f"{refusal} of {request.course_org}."
    if op.scope == SEMESTER:
        if _member(request.semester_org, INSTRUCTORS_TEAM, actor):
            return None
        return f"{refusal} of {request.semester_org}, or course-admin."
    teams = list_teams(request.course_org) or {}
    for slug in sorted(teams):
        term_team = slug == INSTRUCTORS_TEAM or slug.startswith(f"{INSTRUCTORS_TEAM}-")
        if term_team and _member(request.course_org, slug, actor):
            return None
    return f"{refusal} of any semester of {request.course_org}, or course-admin."
