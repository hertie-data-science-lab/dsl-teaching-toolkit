"""Parse and authorise a `dsl.request/1` - the one string the Console workflow is handed.

The request is user text arriving through a public workflow's input, so it is validated
here in full before anything runs: its own shape, then the op's `args_schema`, then who
is asking. The validator is a deliberately small JSON Schema subset (type, enum, pattern,
properties, required, additionalProperties) - enough for the schemas this package writes,
and no new runtime dependency on every runner.
"""

from __future__ import annotations

import json
import re

from ..course import COURSE_ADMIN_TEAM, INSTRUCTORS_TEAM
from ..discovery import discover_cohorts
from ..gh_teams import get_team_members, list_teams
from .registry import (
    BOOTSTRAP_OP,
    COHORT,
    COURSE,
    ORG_PATTERN,
    REGISTRY,
    REQUEST_SCHEMA,
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
        "cohort_org": {"type": "string", "pattern": ORG_PATTERN},
        "args": {"type": "object"},
        "preview": {"type": "boolean"},
        "client": {"type": "string", "pattern": r"^[A-Za-z0-9._/+-]{0,64}$"},
    },
    "required": ["schema", "op", "actor", "course_org", "args", "preview"],
    "additionalProperties": False,
}

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


class RequestError(ValueError):
    """A request the engine refuses before running anything. `code` is the outcome's
    reason code; the message is the sentence that goes with it."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


def _is(value: object, kind: str) -> bool:
    # bool is an int in Python and never an integer in JSON Schema.
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[kind])


def validate(value: object, schema: dict, where: str = "$") -> list[str]:
    """Every way `value` breaks `schema`, as `path: problem` lines. Empty means valid.

    The lines name the path and the rule, never the value: a request can carry a handle."""
    problems: list[str] = []
    kind = schema.get("type")
    if kind:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is(value, k) for k in kinds):
            return [f"{where}: must be {' or '.join(kinds)}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(
            f"{where}: must be one of {', '.join(map(str, schema['enum']))}"
        )
    if (
        "pattern" in schema
        and isinstance(value, str)
        and not re.search(schema["pattern"], value)
    ):
        problems.append(f"{where}: does not match the expected form")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{where}.{key}: is required")
        for key, item in value.items():
            if key in props:
                problems += validate(item, props[key], f"{where}.{key}")
            elif schema.get("additionalProperties") is False:
                problems.append(f"{where}.{key}: is not a known field")
            elif isinstance(schema.get("additionalProperties"), dict):
                problems += validate(
                    item, schema["additionalProperties"], f"{where}.{key}"
                )
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += validate(item, schema["items"], f"{where}[{i}]")
    return problems


def parse_request(text: str) -> Request:
    """A `Request` from the workflow's input, or `RequestError` saying what is wrong."""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise RequestError("BAD_REQUEST", "The request is not valid JSON.") from None
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
    if op.scope == COHORT and not raw.get("cohort_org"):
        raise RequestError("BAD_REQUEST", f"{op.name} needs a cohort_org.")
    if op.scope == COURSE and raw.get("cohort_org"):
        raise RequestError(
            "BAD_REQUEST", f"{op.name} is course-wide and takes no cohort_org."
        )
    if raw["preview"] and op.preview_flag is None:
        raise RequestError(
            "NO_PREVIEW", f"{op.name} has no preview; ask for it without one."
        )
    return Request(
        op=op.name,
        actor=raw["actor"],
        course_org=raw["course_org"],
        cohort_org=raw.get("cohort_org"),
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

    The cohort must be registered under the course. Course admins may run everything. Otherwise an op whose `required_team` is the
    instructors team needs the actor in the COHORT's instructors team, or - for a
    course-wide op - in one of the course org's `instructors-<term>` teams, which is where
    Sync membership mirrors every cohort's teaching team. A team that cannot be read counts
    as "not a member": this gate fails closed."""
    op = REGISTRY[request.op]
    actor = request.actor
    # The cohort must be this course's: a course admin here is nobody in another course.
    # Bootstrap is the one op whose cohort is not registered yet - it registers it.
    if op.scope == COHORT and op.name != BOOTSTRAP_OP:
        registered = {c.casefold() for c in discover_cohorts(request.course_org)}
        if request.cohort_org.casefold() not in registered:
            return f"{request.cohort_org} is not a cohort of {request.course_org}."
    if _member(request.course_org, COURSE_ADMIN_TEAM, actor):
        return None
    refusal = f"@{actor} may not run {op.name}: it needs the {op.required_team} team"
    if op.required_team != INSTRUCTORS_TEAM:
        return f"{refusal} of {request.course_org}."
    if op.scope == COHORT:
        if _member(request.cohort_org, INSTRUCTORS_TEAM, actor):
            return None
        return f"{refusal} of {request.cohort_org}, or course-admin."
    teams = list_teams(request.course_org) or {}
    for slug in sorted(teams):
        term_team = slug == INSTRUCTORS_TEAM or slug.startswith(f"{INSTRUCTORS_TEAM}-")
        if term_team and _member(request.course_org, slug, actor):
            return None
    return f"{refusal} of any cohort of {request.course_org}, or course-admin."
