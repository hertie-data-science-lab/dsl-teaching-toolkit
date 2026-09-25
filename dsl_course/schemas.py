"""Export the console's contracts as JSON Schema: `python -m dsl_course.schemas --out DIR`.

Three wire shapes (`dsl.request/1`, `dsl.outcome/1`, `dsl.status/1`), the operations
registry (`ops.json`), the engine's names (`names.json`), the words for its values
(`labels.json`) and institution policy (`policy.json`), and one schema per instructor-owned file the console edits. Every
enum and every key set is READ off the constant the engine itself parses with, so a value
added to the engine reaches the console's forms with no second edit; the committed copies
under `console/schemas/` are held to a fresh export by `tests/test_schemas.py`.

The file schemas describe what the parsers ACCEPT, not everything they check: cross-field
and cross-file rules (a solution date after the handout, a handle on the roster) stay with
the engine's own check runs, which remain the verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import materials, policy, records
from .central import TIERS
from .course import (
    ASSIGNMENT_TYPES,
    ASSIGNMENTS_FILE,
    CONFIG_REPO,
    FORMATS,
    INSTRUCTOR_ROLES,
    INSTRUCTORS_FILE,
    JOIN_REPO,
    LABELS,
    SOLUTION_WARNING,
    SUBMIT_VIA,
    TEAM_FORMATIONS,
    VISIBILITIES,
)
from .discovery import SEMESTERS_PATH
from .grades import TEMPLATE_KEYS
from .log import CLIParser, log_ok
from .ops.outcome import CONCLUSIONS
from .ops.registry import (
    HANDLE_PATTERN,
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
    KNOWN_ROW_KINDS,
    KNOWN_TOP_LEVEL,
)
from .settings import (
    ASSIGNMENT_DEFAULTS_KEY,
    ASSIGNMENTS_BLOCKS,
    ASSIGNMENTS_DEFAULTS,
    COURSE_DEFAULT_KEYS,
    INSTANCE_KEYS,
    RUN_KEYS,
    SOURCES,
)
from .teams import FIELDS as TEAMS_FIELDS

DRAFT = "https://json-schema.org/draft/2020-12/schema"
DEFAULT_OUT = Path("console/schemas")

# The schema-level types of the keys whose parser accepts more than a string. Anything not
# named here is a string: the parsers read dates, paths and titles as text.
_FLAGS = {"tbc", "show_on_site"}
_EVENT_KINDS = ("exam", "special_event")
# instructors.yml entry keys (sync_faculty and the site read them by name; no constant holds them).
PEOPLE_ENTRY_KEYS = (
    "github_handle",
    "role",
    "email",
    "name",
    "title",
    "photo",
    "url",
    "start",
    "end",
    "show_email",
)
PEOPLE_REQUIRED = ("github_handle", "role", "email")
COURSE_ADMIN_KEYS = ("github_handle", "email", "start", "end")
# Course cards, instructors and TAs alike: what `site_repo._people_from_meta` reads, the
# optional start/end bounding when a card shows.
COURSE_CARD_KEYS = ("github_handle", "name", "title", "photo", "url", "start", "end")
# dsl-course.yml keys beyond `people` and `assignment_defaults`.
COURSE_TOP_KEYS = (
    "course_name",
    "course_code",
    "central_ref",
    "course_description",
    "site_link_extensions",
)
# The assignment_defaults keys New assignment reads as the course's defaults for the
# questions it asks (contracts section 6); optional, beside COURSE_DEFAULT_KEYS.
ASKED_DEFAULT_KEYS = ("formats", "submit_via", "team_formation", "visibility")


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
                "details": {"type": "array", "items": _str()},
                "block": _str(),
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
PROBLEM_SCOPES = ("course", "semester")


def status_schema() -> dict:
    """Hand-written from contracts section 3; the status writer (WP2) keeps it honest:
    `tests/test_status_json.py` validates its render against this. No automation
    heartbeat - it moves every tick, and the console reads it off the run list."""
    stages = {"type": "object", "additionalProperties": _enum(STAGE_STATES)}
    # Why each stage that is not done is not: one sentence per stage id. Optional.
    stage_why = {"type": "object", "additionalProperties": _str()}
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
            "stage_why": stage_why,
            "ready": {"type": "boolean"},
            "materials": {"type": "array", "items": repo_state},
            "templates": {"type": "array", "items": repo_state},
            "semesters": {"type": "array", "items": _str()},
        },
        ("org", "stages"),
    )
    semester = _obj(
        {
            "org": _str(),
            "key": nullable,
            "label": nullable,
            "timezone": _str(),
            "week": {"type": ["integer", "null"]},
            "weeks": {"type": ["integer", "null"]},
            "live": {"type": "boolean"},
            "app_installed": unknown,
            "stages": stages,
            "stage_why": stage_why,
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
            "kind": _str(),
            "ref": _str(),
            "title": _str(),
            "state": _str(),
        },
        ("when", "kind", "ref", "state"),
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
            "kind": _str(),
            "kind_inferred": {"type": "boolean"},
            "title": _str(),
            "state": _enum(RELEASE_STATES),
            "source": place,
            "dest": place,
            "copies": {"type": "integer"},
            "show_on_site": {"type": "boolean"},
            "tbc": {"type": "boolean"},
        },
        # `when` always present (null while TBC): the console derives `late` from it.
        ("id", "state", "when"),
    )
    assignment = _obj(
        {
            "slug": _str(),
            "title": _str(),
            "template": _str(),
            "state": _enum(ASSIGNMENT_STATES),
            "handout": nullable,
            "due": nullable,
            "grading_cutoff_datetime": nullable,
            "solution_shown": nullable,
            "units": {"type": ["integer", "null"]},
            "submissions": {"type": ["integer", "null"]},
            "teams": {"type": ["integer", "null"]},
            "marks": _obj(
                {"filled": {"type": "integer"}, "total": {"type": "integer"}}
            ),
            "returned": {"type": "boolean"},
            "problem": {"type": "boolean"},
            # Each run setting's effective value and the layer that gave it (`settings`).
            "settings": _obj(
                {
                    key: _obj(
                        {
                            "value": {
                                "type": ["string", "integer", "null"],
                            },
                            "source": _enum(SOURCES),
                        },
                        ("value", "source"),
                    )
                    for key in RUN_KEYS
                }
            ),
        },
        # The four moments are always present (null when unset), so the console can move
        # an assignment from open to late window to marking on its own clock.
        (
            "slug",
            "state",
            "handout",
            "due",
            "grading_cutoff_datetime",
            "solution_shown",
        ),
    )
    count = {"type": "integer"}
    return _doc(
        STATUS_SCHEMA,
        _obj(
            {
                "schema": {"type": "string", "enum": [STATUS_SCHEMA]},
                "inputs": {"type": "object", "additionalProperties": nullable},
                "course": course,
                "semester": semester,
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
                "kind": _enum(KNOWN_ROW_KINDS),
                "deploy": {"type": "array", "items": deploy},
                "event_datetime": _str(),
            },
        )
    )
    marks_row = _obj({"event_datetime": _str(), "show_on_site": {"type": "boolean"}})
    assignment = _obj(
        _keys(
            KNOWN_ASSIGNMENT,
            {"marks_return_datetime": {"oneOf": [_str(), marks_row]}},
        ),
        ("due_datetime", "course_source_repo"),
    )
    event = _obj(_keys(KNOWN_EVENT, {"kind": _enum(_EVENT_KINDS)}))
    archive = _obj(_keys(KNOWN_ARCHIVE, {"grace_days": {"type": "integer"}}))
    top = _keys(
        KNOWN_TOP_LEVEL,
        {
            "releases": {"type": "object", "additionalProperties": release},
            "assignments": {"type": "object", "additionalProperties": assignment},
            "events": {"type": "object", "additionalProperties": event},
            "archive": archive,
        },
    )
    return _doc(f"{CONFIG_REPO}/schedule.yml", _obj(top))


def instructors_schema() -> dict:
    entry = _obj(
        _keys(
            PEOPLE_ENTRY_KEYS,
            {
                "show_email": {"type": "boolean"},
                "role": {"type": "string", "enum": list(INSTRUCTOR_ROLES)},
            },
        ),
        PEOPLE_REQUIRED,
    )
    return _doc(
        f"{CONFIG_REPO}/{INSTRUCTORS_FILE}",
        _obj({"instructors": {"type": "array", "items": entry}}),
    )


def _csv_schema(title: str, fields, required, overrides: dict | None = None) -> dict:
    """A CSV as its header and one row: `columns` is the header the engine writes, and a
    row is an object of those columns."""
    row = _obj(_keys(fields, overrides), required)
    return _doc(title, {"type": "array", "items": row, "columns": list(fields)})


def students_schema() -> dict:
    return _csv_schema(
        f"{CONFIG_REPO}/students.csv",
        ROSTER_FIELDS,
        ROSTER_REQUIRED,
        {"role": _enum(("", ROLE_ENROLLED, ROLE_AUDITOR))},
    )


def teams_schema() -> dict:
    return _csv_schema(f"{CONFIG_REPO}/teams.csv", TEAMS_FIELDS, TEAMS_FIELDS)


_SPEC_TYPES = {
    "type": _enum(ASSIGNMENT_TYPES),
    "team_formation": _enum(TEAM_FORMATIONS),
    "max_team_size": {"type": "integer"},
    "submit_via": _enum(SUBMIT_VIA),
    "visibility": _enum(VISIBILITIES),
    # A list in grading_config.yml; the course's assignment_defaults may also give the
    # New assignment box's comma-separated string.
    "formats": {
        "oneOf": [
            {"type": "array", "items": _enum(FORMATS)},
            {"type": "string"},
        ]
    },
    # `Q1: 15`, or `Q1: {points: 15, file: report.tex}` for a question marked from a
    # file other than the runnable starter.
    "questions": {
        "type": "object",
        "additionalProperties": {
            "oneOf": [
                {"type": ["string", "number"]},
                _obj(
                    {
                        "points": {"type": ["string", "number"]},
                        "file": {"type": "string"},
                    }
                ),
            ]
        },
    },
    "late_window_days": {"type": "integer"},
    "late_penalty_per_day": {"type": ["string", "number"]},
    "autograde": {"type": "boolean"},
    "completion_check": {"type": "boolean"},
    "grader_pdf": {"type": "boolean"},
}


def grading_config_schema() -> dict:
    return _doc("grading_config.yml", _obj(_keys(TEMPLATE_KEYS, _SPEC_TYPES)))


def assignments_schema() -> dict:
    """`semester-config/assignments.yml`: this semester's `defaults:` and one block per
    schedule key (`settings.parse_instance`)."""
    defaults = _obj(_keys(RUN_KEYS, _SPEC_TYPES))
    block = _obj(_keys(INSTANCE_KEYS, _SPEC_TYPES))
    return _doc(
        f"{CONFIG_REPO}/{ASSIGNMENTS_FILE}",
        _obj(
            {
                ASSIGNMENTS_DEFAULTS: defaults,
                ASSIGNMENTS_BLOCKS: {"type": "object", "additionalProperties": block},
            }
        ),
    )


def dsl_course_schema() -> dict:
    admin = _obj(_keys(COURSE_ADMIN_KEYS), ("github_handle",))
    card = _obj(_keys(COURSE_CARD_KEYS))
    people = _obj(
        {
            "course_admins": {"type": "array", "items": admin},
            "instructors": {"type": "array", "items": card},
            "teaching_assistants": {"type": "array", "items": card},
        }
    )
    defaults = _obj(_keys((*COURSE_DEFAULT_KEYS, *ASKED_DEFAULT_KEYS), _SPEC_TYPES))
    central_ref = {
        "type": "string",
        "pattern": f"^(?:{'|'.join(TIERS)}|[0-9a-f]{{40}})$",
    }
    top = _keys(
        COURSE_TOP_KEYS,
        {
            "central_ref": central_ref,
            "site_link_extensions": {"type": "array", "items": _str()},
        },
    )
    top |= {
        "people": people,
        ASSIGNMENT_DEFAULTS_KEY: defaults,
    }
    return _doc(".github/dsl-course.yml", _obj(top))


def materials_schema() -> dict:
    """A materials repo's optional `materials.yml`: the syllabus file and folder -> kind
    aliases (`materials.parse`)."""
    body = dict(materials.SCHEMA)
    body["properties"] = {
        "syllabus": _str(),
        "kinds": {
            "type": "object",
            "additionalProperties": _enum(policy.content_kinds()),
        },
    }
    return _doc(materials.MATERIALS_FILE, body)


def names_json() -> dict:
    """The repo names and paths the console must spell exactly as the engine does, so it
    holds no literal of its own: the config and join repos, the `.system/` folder and every
    record in it (`records.path`), and the instructor files."""
    return {
        "config_repo": CONFIG_REPO,
        "join_repo": JOIN_REPO,
        "system_dir": records.SYSTEM_DIR,
        "instructors_file": INSTRUCTORS_FILE,
        "assignments_file": ASSIGNMENTS_FILE,
        "registry_file": SEMESTERS_PATH,
        "records": {kind: records.path(kind) for kind in records.RECORDS},
    }


def labels_json() -> dict:
    """The words the console shows for the engine's values (`course.LABELS`) and the
    solution warning every surface offering `solution_datetime: now` carries."""
    return {"solution_warning": SOLUTION_WARNING, **LABELS}


def all_schemas() -> dict[str, dict]:
    """Every exported file, by its name under the output directory."""
    return {
        "request.schema.json": request_schema(),
        "outcome.schema.json": outcome_schema(),
        "status.schema.json": status_schema(),
        "ops.json": ops_json(),
        "names.json": names_json(),
        "labels.json": labels_json(),
        # The institution policy the engine runs on (`policy.load`): the defaults, kinds,
        # site block, contact and licences, so the console holds no literal of its own.
        "policy.json": policy.load(),
        "schedule.schema.json": schedule_schema(),
        "instructors.schema.json": instructors_schema(),
        "students.schema.json": students_schema(),
        "teams.schema.json": teams_schema(),
        "grading_config.schema.json": grading_config_schema(),
        "assignments.schema.json": assignments_schema(),
        "dsl_course.schema.json": dsl_course_schema(),
        "materials.schema.json": materials_schema(),
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
    parser = CLIParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="Output directory"
    )
    args = parser.parse_args()
    written = write(args.out)
    log_ok(f"Done - wrote {len(written)} schema files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
