"""Export the console's contracts as JSON Schema: `python -m dsl_course.schemas --out DIR`.

Three wire shapes (`dsl.request/1`, `dsl.outcome/1`, `dsl.status/1`), the operations
registry (`ops.json`), and one schema per instructor-owned file the console edits. Every
enum and every key set is READ off the constant the engine itself parses with, so a value
added to the engine reaches the console's forms with no second edit; the committed copies
under `console/schemas/` are held to a fresh export by `tests/test_schemas.py`.

The file schemas describe what the parsers ACCEPT, not everything they check: cross-field
and cross-file rules (a solution date after the handout, a handle on the roster) stay with
the engine's own check runs, which remain the verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .central import TIERS
from .course import (
    ASSIGNMENT_TYPES,
    FORMATS,
    SUBMIT_VIA,
    TEAM_FORMATIONS,
    VISIBILITIES,
)
from .grades import COURSE_DEFAULT_KEYS, SPEC_KEYS
from .log import log_ok
from .ops.outcome import CONCLUSIONS
from .ops.registry import (
    HANDLE_PATTERN,
    ORG_PATTERN,
    OUTCOME_SCHEMA,
    REGISTRY,
    STATUS_SCHEMA,
    public_view,
)
from .ops.request import REQUEST_JSON_SCHEMA
from .roster import FIELDS as ROSTER_FIELDS
from .roster import REQUIRED_FIELDS as ROSTER_REQUIRED
from .roster import ROLE_AUDITOR, ROLE_ENROLLED
from .schedule import (
    KNOWN_ARCHIVE,
    KNOWN_ASSIGNMENT,
    KNOWN_DEPLOY,
    KNOWN_EVENT,
    KNOWN_RELEASE,
    KNOWN_RELEASE_TYPES,
    KNOWN_TOP_LEVEL,
)
from .sync_faculty import TEACHING_ROLES
from .teams import FIELDS as TEAMS_FIELDS

DRAFT = "https://json-schema.org/draft/2020-12/schema"
DEFAULT_OUT = Path("console/schemas")

# The schema-level types of the keys whose parser accepts more than a string. Anything not
# named here is a string: the parsers read dates, paths and titles as text.
_FLAGS = {"tbc", "show_on_site"}
_EVENT_TYPES = ("exam", "special_event")
# people.yml entry keys (sync_faculty and the site read them by name; no constant holds them).
PEOPLE_ENTRY_KEYS = (
    "github_handle",
    "email",
    "name",
    "title",
    "photo",
    "url",
    "start",
    "end",
    "show_email",
)
PEOPLE_REQUIRED = ("github_handle", "email")
COURSE_ADMIN_KEYS = ("github_handle", "email", "start", "end")
COURSE_CARD_KEYS = ("github_handle", "name", "title", "photo", "url")
# dsl-course.yml keys beyond `people`, `assignment_defaults` and `cohort_defaults`.
COURSE_TOP_KEYS = (
    "org",
    "org_name",
    "course_name",
    "course_code",
    "central_ref",
    "course_description",
    "site_link_extensions",
)
# The assignment_defaults keys New assignment reads as the course's defaults for the
# questions it asks (contracts section 6); optional, beside COURSE_DEFAULT_KEYS.
ASKED_DEFAULT_KEYS = ("format", "submit_via", "team_formation", "visibility")


def _obj(
    properties: dict, required: tuple[str, ...] | list[str] = (), *, closed: bool = True
) -> dict:
    out: dict = {"type": "object", "properties": properties}
    if required:
        out["required"] = list(required)
    if closed:
        out["additionalProperties"] = False
    return out


def _enum(values) -> dict:
    return {
        "type": "string",
        "enum": sorted(values)
        if isinstance(values, (set, frozenset))
        else list(values),
    }


def _str() -> dict:
    return {"type": "string"}


def _keys(keys, overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    return {
        k: overrides.get(k, {"type": "boolean"} if k in _FLAGS else _str())
        for k in sorted(keys)
    }


def _doc(title: str, body: dict) -> dict:
    return {"$schema": DRAFT, "title": title, **body}


# ------------------------------------------------------------------ wire shapes


def request_schema() -> dict:
    return _doc("dsl.request/1", REQUEST_JSON_SCHEMA)


def outcome_schema() -> dict:
    fix = _obj(
        {
            "repo": _str(),
            "path": _str(),
            "line": {"type": "integer"},
            "screen": _str(),
            "entry": _str(),
        }
    )
    reason = _obj(
        {
            "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
            "text": _str(),
            "fix": fix,
        },
        ("code", "text"),
    )
    person = _obj(
        {"handle": {"type": "string", "pattern": HANDLE_PATTERN}, "text": _str()},
        ("handle", "text"),
    )
    return _doc(
        OUTCOME_SCHEMA,
        _obj(
            {
                "schema": {"type": "string", "enum": [OUTCOME_SCHEMA]},
                "op": _str(),
                "run_id": {"type": ["integer", "null"]},
                "actor": _str(),
                "preview": {"type": "boolean"},
                "conclusion": _enum(CONCLUSIONS),
                "summary": _str(),
                "counts": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                "reasons": {"type": "array", "items": reason},
                "people": {"type": "array", "items": person},
                "started": _str(),
                "finished": _str(),
            },
            ("schema", "op", "actor", "preview", "conclusion", "summary"),
        ),
    )


STAGE_STATES = ("done", "todo", "blocked", "problem")
ASSIGNMENT_STATES = (
    "declared",
    "teams_forming",
    "blocked",
    "open",
    "late_window",
    "marking",
    "returned",
)
RELEASE_STATES = ("planned", "will_be_skipped", "released", "late")
PROBLEM_SCOPES = ("course", "cohort")


def status_schema() -> dict:
    """Hand-written from contracts section 3; the status writer (WP2) keeps it honest:
    `tests/test_status_json.py` validates its render against this. No automation
    heartbeat - it moves every tick, and the console reads it off the run list."""
    stages = {"type": "object", "additionalProperties": _enum(STAGE_STATES)}
    nullable = {"type": ["string", "null"]}
    unknown = {"type": ["boolean", "null"]}  # `app_installed` until decision 0002
    # A status problem's pointer: the line, screen and entry are what the fault knows,
    # and `ref` names the branch when it is not the default (a template's `solution`).
    # `url` is a fix that is a GitHub settings page rather than a file (its `path` is "").
    fix = _obj(
        {
            "repo": _str(),
            "path": _str(),
            "line": {"type": ["integer", "null"]},
            "screen": nullable,
            "entry": nullable,
            "ref": _str(),
            "url": _str(),
        },
        ("repo", "path"),
    )
    repo_state = _obj(
        {"repo": _str(), "slug": _str(), "state": _str()}, ("repo", "state")
    )
    course = _obj(
        {
            "org": _str(),
            "name": _str(),
            "code": _str(),
            "app_installed": unknown,
            "stages": stages,
            "ready": {"type": "boolean"},
            "materials": {"type": "array", "items": repo_state},
            "templates": {"type": "array", "items": repo_state},
            "cohorts": {"type": "array", "items": _str()},
        },
        ("org", "stages"),
    )
    cohort = _obj(
        {
            "org": _str(),
            "term": nullable,
            "term_label": nullable,
            "timezone": _str(),
            "week": {"type": ["integer", "null"]},
            "weeks": {"type": ["integer", "null"]},
            "live": {"type": "boolean"},
            "app_installed": unknown,
            "stages": stages,
            "archive_date": nullable,
        },
        ("org", "stages", "live"),
    )
    problem = _obj(
        {
            "id": _str(),
            "scope": _enum(PROBLEM_SCOPES),
            # A setup stage (C1-C6, K1-K7) or a running phase (`marking`); never null.
            "stage": _str(),
            "text": _str(),
            "stops": _str(),
            "fix": fix,
        },
        ("id", "scope", "stage", "text"),
    )
    week_item = _obj(
        {
            "when": _str(),
            "type": _str(),
            "ref": _str(),
            "title": _str(),
            "state": _str(),
        },
        ("when", "type", "ref", "state"),
    )
    # An entry with nothing to copy has no source or destination.
    place = {
        **_obj({"repo": _str(), "path": _str()}, ("repo",)),
        "type": ["object", "null"],
    }
    release = _obj(
        {
            "id": _str(),
            "when": nullable,
            "type": nullable,
            "title": _str(),
            "state": _enum(RELEASE_STATES),
            "source": place,
            "dest": place,
            "copies": {"type": "integer"},
            "show_on_site": {"type": "boolean"},
            "tbc": {"type": "boolean"},
        },
        ("id", "state"),
    )
    assignment = _obj(
        {
            "slug": _str(),
            "title": _str(),
            "template": _str(),
            "state": _enum(ASSIGNMENT_STATES),
            "handout": nullable,
            "due": nullable,
            "late_until": nullable,
            "solution_shown": nullable,
            "units": {"type": ["integer", "null"]},
            "submissions": {"type": ["integer", "null"]},
            "teams": {"type": ["integer", "null"]},
            "marks": _obj(
                {"filled": {"type": "integer"}, "total": {"type": "integer"}}
            ),
            "returned": {"type": "boolean"},
            "problem": {"type": "boolean"},
        },
        ("slug", "state"),
    )
    count = {"type": "integer"}
    return _doc(
        STATUS_SCHEMA,
        _obj(
            {
                "schema": {"type": "string", "enum": [STATUS_SCHEMA]},
                "inputs": {"type": "object", "additionalProperties": nullable},
                "course": course,
                "cohort": cohort,
                "problems": {"type": "array", "items": problem},
                "this_week": {"type": "array", "items": week_item},
                "releases": {"type": "array", "items": release},
                "assignments": {"type": "array", "items": assignment},
                "students": _obj({"rows": count, "codes_sent": count, "joined": count}),
                "staff": _obj({"instructors": count, "tas": count, "synced": unknown}),
                "site": _obj(
                    {
                        "url": _str(),
                        "last_update": nullable,
                        "stale": {"type": "boolean"},
                    }
                ),
                "operations": {
                    "type": "array",
                    "items": _obj(
                        {
                            "run_id": {"type": ["integer", "null"]},
                            "op": _str(),
                            "conclusion": _enum(CONCLUSIONS),
                            "summary": _str(),
                            "finished": _str(),
                        },
                        ("op", "conclusion"),
                    ),
                },
            },
            ("schema", "inputs", "course", "problems"),
        ),
    )


def ops_json() -> dict:
    return {"ops": [public_view(op) for op in REGISTRY.values()]}


# ------------------------------------------------------------------ instructor-owned files


def schedule_schema() -> dict:
    deploy = _obj(_keys(KNOWN_DEPLOY), ("course_source_repo", "course_source_path"))
    release = _obj(
        _keys(
            KNOWN_RELEASE,
            {
                "type": _enum(KNOWN_RELEASE_TYPES),
                "deploy": {"type": "array", "items": deploy},
                "event_datetime": _str(),
            },
        )
    )
    assignment = _obj(_keys(KNOWN_ASSIGNMENT), ("due_datetime", "course_source_repo"))
    event = _obj(_keys(KNOWN_EVENT, {"type": _enum(_EVENT_TYPES)}))
    archive = _obj(_keys(KNOWN_ARCHIVE, {"grace_days": {"type": "integer"}}))
    top = _keys(
        KNOWN_TOP_LEVEL,
        {
            "releases": {"type": "object", "additionalProperties": release},
            "assignments": {"type": "object", "additionalProperties": assignment},
            "events": {"type": "object", "additionalProperties": event},
            "archive": archive,
            "enrolment": {"description": "Deprecated and ignored."},
        },
    )
    return _doc("classroom-config/schedule.yml", _obj(top))


def people_schema() -> dict:
    entry = _obj(
        _keys(PEOPLE_ENTRY_KEYS, {"show_email": {"type": "boolean"}}), PEOPLE_REQUIRED
    )
    roles = {role: {"type": "array", "items": entry} for role in TEACHING_ROLES}
    return _doc("classroom-config/people.yml", _obj({"people": _obj(roles)}))


def _csv_schema(title: str, fields, required, overrides: dict | None = None) -> dict:
    """A CSV as its header and one row: `columns` is the header the engine writes, and a
    row is an object of those columns."""
    row = _obj(_keys(fields, overrides), required)
    return _doc(title, {"type": "array", "items": row, "columns": list(fields)})


def students_schema() -> dict:
    return _csv_schema(
        "classroom-config/students.csv",
        ROSTER_FIELDS,
        ROSTER_REQUIRED,
        {"role": _enum(("", ROLE_ENROLLED, ROLE_AUDITOR))},
    )


def teams_schema() -> dict:
    return _csv_schema("classroom-config/teams.csv", TEAMS_FIELDS, TEAMS_FIELDS)


_SPEC_TYPES = {
    "type": _enum(ASSIGNMENT_TYPES),
    "team_formation": _enum(TEAM_FORMATIONS),
    "max_team_size": {"type": "integer"},
    "submit_via": _enum(SUBMIT_VIA),
    "visibility": _enum(VISIBILITIES),
    "format": _enum(FORMATS),
    "questions": {
        "type": "object",
        "additionalProperties": {"type": ["string", "number"]},
    },
    "late_window_days": {"type": "integer"},
    "late_penalty_per_day": {"type": ["string", "number"]},
    "autograde": {"type": "boolean"},
    "completion_check": {"type": "boolean"},
    "grader_pdf": {"type": "boolean"},
}


def grading_config_schema() -> dict:
    return _doc("grading_config.yml", _obj(_keys(SPEC_KEYS, _SPEC_TYPES)))


def dsl_course_schema() -> dict:
    admin = _obj(_keys(COURSE_ADMIN_KEYS), ("github_handle",))
    card = _obj(_keys(COURSE_CARD_KEYS))
    people = _obj(
        {
            "course_admins": {"type": "array", "items": admin},
            "instructors": {"type": "array", "items": card},
        }
    )
    defaults = _obj(_keys((*COURSE_DEFAULT_KEYS, *ASKED_DEFAULT_KEYS), _SPEC_TYPES))
    cohort_defaults = _obj(
        {
            "timezone": _str(),
            "archive": _obj(
                {"auto": {"type": "boolean"}, "grace_days": {"type": "integer"}}
            ),
        }
    )
    central_ref = {
        "type": "string",
        "pattern": f"^(?:{'|'.join(TIERS)}|[0-9a-f]{{40}})$",
    }
    top = _keys(
        COURSE_TOP_KEYS,
        {
            "org": {"type": "string", "pattern": ORG_PATTERN},
            "central_ref": central_ref,
            "site_link_extensions": {"type": "array", "items": _str()},
        },
    )
    top |= {
        "people": people,
        "assignment_defaults": defaults,
        "cohort_defaults": cohort_defaults,
    }
    return _doc(".github/dsl-course.yml", _obj(top, ("org",)))


def all_schemas() -> dict[str, dict]:
    """Every exported file, by its name under the output directory."""
    return {
        "request.schema.json": request_schema(),
        "outcome.schema.json": outcome_schema(),
        "status.schema.json": status_schema(),
        "ops.json": ops_json(),
        "schedule.schema.json": schedule_schema(),
        "people.schema.json": people_schema(),
        "students.schema.json": students_schema(),
        "teams.schema.json": teams_schema(),
        "grading_config.schema.json": grading_config_schema(),
        "dsl_course.schema.json": dsl_course_schema(),
    }


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in all_schemas().items():
        path = out / name
        path.write_text(render(schema))
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="Output directory"
    )
    args = parser.parse_args()
    written = write(args.out)
    log_ok(f"Done - wrote {len(written)} schema files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
