"""Every operation the Instructor Console can ask the engine for, one entry each.

A dispatch op names the frozen CLI it runs and spells that CLI's flags exactly as the
seeded workflow for the same job does (`workflows_render`), so the console and the Actions
tab cannot drift into two meanings for one button. `args_schema` is built from the course
vocabulary in `course`, so a new enum value reaches the console's form with no second edit.

The preview gate mirrors `workflows_render._DRY_RUN_GATE`: a CLI whose dry run DEFAULTS ON
is handed `--no-dry-run` explicitly for a real run (`real_flag`), so an argv that lost a
flag previews rather than acts. An op whose CLI has no dry run has `preview_flag=None`, and
the request parser refuses `preview: true` for it (`NO_PREVIEW`).

`via` says where the CLI runs. `inline` runs it inside the Console job. `workflow:<file>`
dispatches that seeded workflow instead, with the inputs the argv maps to: an op that runs
STUDENT CODE needs the sandbox account and the job shape only its own workflow has, so it
never runs in the Console job.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..course import (
    ASSIGNMENT_TYPES,
    COURSE_ADMIN_TEAM,
    INSTRUCTORS_TEAM,
    NOTHING_PUBLIC,
    PUBLIC_DIRS,
    PUBLIC_HTML_PDF,
    PUBLIC_TYPES,
    STARTER_FORMATS,
    SUBMIT_VIA,
    TEAM_FORMATIONS,
    VISIBILITIES,
)

REQUEST_SCHEMA = "dsl.request/1"
OUTCOME_SCHEMA = "dsl.outcome/1"
STATUS_SCHEMA = "dsl.status/1"

DISPATCH = "dispatch"
COURSE = "course"
COHORT = "cohort"
BOOTSTRAP_OP = "cohort.bootstrap"
INLINE = "inline"
VIA_WORKFLOW = "workflow:"

# The patterns every free-text argument is held to. No value may start with `-`: argparse
# would read it as a flag, and a request is user text.
ORG_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"
REPO_PATTERN = r"^(?!-)[A-Za-z0-9._-]{1,100}$"
PATH_PATTERN = r"^(?!-)[^\x00-\x1f]{1,1024}$"
KEY_PATTERN = r"^(?!-)[A-Za-z0-9_.-]{1,100}$"
TAG_PATTERN = r"^[fs][0-9]{4}$"
HANDLE_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"
FORMATS_PATTERN = (
    rf"^(?:none|(?:{'|'.join(STARTER_FORMATS)})(?:,(?:{'|'.join(STARTER_FORMATS)}))*)$"
)
READINGS_MODES = ("reading-list", "actual-readings", "none")


@dataclass(frozen=True)
class Request:
    """A parsed `dsl.request/1`. `args` has been validated against the op's schema."""

    op: str
    actor: str
    course_org: str
    cohort_org: str | None
    args: dict
    preview: bool
    client: str = ""


@dataclass(frozen=True)
class Operation:
    name: str
    runs_as: str
    scope: str
    required_team: str
    args_schema: dict
    help: str
    doc: str
    # The frozen CLI (`python -m dsl_course.<module>`) and its flags, WITHOUT the preview
    # gate - `command` adds that.
    module: str = ""
    argv: Callable[[Request], list[str]] | None = None
    preview_flag: str | None = None
    # Passed on a REAL run, for a CLI whose dry run defaults on.
    real_flag: str | None = None
    # What the op's `counts` keys mean, for the console's details fold.
    counts_doc: str = ""
    # The outcome's summary for a real run whose CLI returned a bare exit code rather
    # than a `log.Summary` (an early exit, or a CLI that has no sentence of its own).
    done_text: str = ""
    # Ends in `seed refresh`, as its workflow does, so a new repo gets its buttons.
    refresh_after: bool = False
    via: str = INLINE
    # For a `workflow:` op, the dispatch inputs `workflow_inputs` fills from the argv.
    inputs: tuple[str, ...] = ()

    @property
    def workflow(self) -> str | None:
        """The seeded workflow file a `workflow:` op dispatches, else None."""
        return (
            self.via.removeprefix(VIA_WORKFLOW)
            if self.via.startswith(VIA_WORKFLOW)
            else None
        )


def command(op: Operation, request: Request) -> list[str]:
    """The whole argv the target CLI is run with: its flags plus the preview gate."""
    argv = op.argv(request) if op.argv else []
    if request.preview:
        if op.preview_flag and op.preview_flag not in argv:
            argv.append(op.preview_flag)
    elif op.real_flag:
        argv.append(op.real_flag)
    return argv


def workflow_inputs(op: Operation, argv: list[str]) -> dict[str, str]:
    """The dispatch inputs of a `workflow:` op, read off the argv its workflow would build:
    `--name value` fills input `name` (dashes to underscores), a bare `--name` switch is
    "true", and an input the argv does not spell is left to the workflow's own default."""
    out: dict[str, str] = {}
    for i, token in enumerate(argv):
        if not token.startswith("--"):
            continue
        name = token[2:].replace("-", "_")
        if name not in op.inputs:
            continue
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        out[name] = "true" if nxt is None or nxt.startswith("--") else nxt
    return out


# ------------------------------------------------------------------ schema builders


def _string(pattern: str | None = None, description: str = "") -> dict:
    out: dict = {"type": "string"}
    if pattern:
        out["pattern"] = pattern
    if description:
        out["description"] = description
    return out


def _enum(values: tuple[str, ...] | list[str], description: str = "") -> dict:
    out: dict = {"type": "string", "enum": list(values)}
    if description:
        out["description"] = description
    return out


def _boolean(description: str = "") -> dict:
    out: dict = {"type": "boolean"}
    if description:
        out["description"] = description
    return out


def _args(properties: dict | None = None, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


_TEMPLATE = _string(REPO_PATTERN, "The course-org assignment template repo")
_SLUG = _string(
    KEY_PATTERN, "The schedule.yml assignment key, when two share a template"
)
_DEPLOY_FIELDS = {
    "course_source_repo": _string(REPO_PATTERN, "Course-org repo to release from"),
    "course_source_path": _string(PATH_PATTERN, "Path(s), comma-separated"),
    "cohort_dest_repo": _string(REPO_PATTERN, "Cohort repo to release into"),
    "cohort_dest_path": _string(PATH_PATTERN, "Destination path(s), comma-separated"),
}


# ------------------------------------------------------------------ argv builders


def _a(request: Request, key: str, default=None):
    return request.args.get(key, default)


def _course_cohort(request: Request) -> list[str]:
    return ["--course-org", request.course_org, "--cohort-org", request.cohort_org]


def _status_write(request: Request) -> list[str]:
    return [*_course_cohort(request), "--write"]


def _scheduler(request: Request) -> list[str]:
    return [*_course_cohort(request), "--skip-autograde", "--dry-run"]


def _deploy(request: Request) -> list[str]:
    return [
        "--source-org",
        request.course_org,
        "--course-source-repo",
        _a(request, "course_source_repo"),
        "--cohort-org",
        request.cohort_org,
        "--course-source-path",
        _a(request, "course_source_path"),
        "--cohort-dest-repo",
        _a(request, "cohort_dest_repo", "materials"),
        "--cohort-dest-path",
        _a(request, "cohort_dest_path", ""),
    ]


def _assign_base(request: Request) -> list[str]:
    return [
        "--master-org",
        request.course_org,
        "--course-source-repo",
        _a(request, "course_source_repo"),
        "--cohort-org",
        request.cohort_org,
    ]


def _handout(request: Request) -> list[str]:
    argv = _assign_base(request)
    if _a(request, "include_solution"):
        argv.append("--solution")
    return argv


def _patch(request: Request) -> list[str]:
    argv = [*_assign_base(request), "--patch-path", _a(request, "path")]
    if _a(request, "slug"):
        argv += ["--slug", _a(request, "slug")]
    if _a(request, "overwrite"):
        argv.append("--overwrite")
    return argv


def _collect(request: Request) -> list[str]:
    argv = [*_assign_base(request), "--refresh-only"]
    if _a(request, "slug"):
        argv += ["--slug", _a(request, "slug")]
    return argv


def _grades(request: Request) -> list[str]:
    argv = ["distribute", "--cohort-org", request.cohort_org]
    if not _a(request, "notify", True):
        argv.append("--no-notify")
    if _a(request, "receipt_note"):
        argv.append("--receipt-note")
    if _a(request, "include_feedback"):
        argv.append("--include-feedback")
    return argv


def _send_codes(request: Request) -> list[str]:
    return [
        "--cohort-org",
        request.cohort_org,
        "--dispatched-by",
        request.course_org,
        "--resend-unjoined",
    ]


def _site_sync(request: Request) -> list[str]:
    return ["sync", *_course_cohort(request)]


def _teardown(request: Request) -> list[str]:
    argv = _course_cohort(request)
    if _a(request, "force"):
        argv.append("--force")
    return argv


def _publish(request: Request) -> list[str]:
    argv = [
        "public-sync",
        "--course-org",
        request.course_org,
        "--source-repo",
        _a(request, "source_repo"),
        "--readings-mode",
        _a(request, "readings_mode", "reading-list"),
    ]
    if not _a(request, "include_lectures", True):
        argv.append("--no-include-lectures")
    return argv


def _derive(request: Request) -> list[str]:
    return [
        "--course-org",
        request.course_org,
        "--course-source-repo",
        _a(request, "course_source_repo"),
    ]


def _syllabus(request: Request) -> list[str]:
    return [
        *_course_cohort(request),
        "--course-source-repo",
        _a(request, "course_source_repo"),
    ]


def _new_materials(request: Request) -> list[str]:
    argv = [
        "materials",
        "--org",
        request.course_org,
        "--tag",
        _a(request, "tag"),
        "--public-dirs",
        _a(request, "public_dirs", NOTHING_PUBLIC),
        "--public-types",
        _a(request, "public_types", PUBLIC_HTML_PDF),
    ]
    if _a(request, "copy_from"):
        argv += ["--copy-from", _a(request, "copy_from")]
    return argv


def _new_assignment(request: Request) -> list[str]:
    argv = [
        "assignment",
        "--org",
        request.course_org,
        "--number",
        str(_a(request, "number")),
        "--tag",
        _a(request, "tag"),
        "--name",
        _a(request, "name", ""),
        "--format",
        _a(request, "format", "ipynb"),
        "--type",
        _a(request, "type", "individual"),
        "--team-formation",
        _a(request, "team_formation", "self_select"),
        "--submit-via",
        _a(request, "submit_via", "assignment_repo"),
        "--visibility",
        _a(request, "visibility", "private"),
        "--autograde",
        "true" if _a(request, "autograde") else "false",
    ]
    if _a(request, "copy_from"):
        argv += ["--copy-from", _a(request, "copy_from")]
    return argv


def _bootstrap_cohort(request: Request) -> list[str]:
    return [
        "--org",
        request.cohort_org,
        "--org-name",
        request.cohort_org,
        "--cohort",
        "--course",
        request.course_org,
        "--propagate-secret",
    ]


def _open_window(request: Request) -> list[str]:
    return [*_course_cohort(request), "--assignment", _a(request, "assignment")]


# ------------------------------------------------------------------ the registry

_RELEASE_ENTRY_ARGS = _args(
    {"entry": _string(KEY_PATTERN, "The schedule.yml releases key"), **_DEPLOY_FIELDS},
    required=("entry",),
)
_RELEASE_COUNTS = "Copied paths per destination, as deploy reports them."


def _release(name: str, help_text: str, args_schema: dict) -> Operation:
    return Operation(
        name=name,
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=args_schema,
        help=help_text,
        doc="docs/08-release-materials-to-cohort.md",
        module="deploy",
        argv=_deploy,
        preview_flag="--dry-run",
        counts_doc=_RELEASE_COUNTS,
        done_text="Materials released.",
    )


_OPS = (
    Operation(
        name="cohort.check",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(),
        help="Check the cohort's setup and refresh what the console shows.",
        done_text="Status refreshed.",
        doc="docs/reference/actions-reference.md",
        module="status",
        argv=_status_write,
    ),
    Operation(
        name="cohort.preview_automation",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(),
        help="Preview what automation would release in this cohort now.",
        doc="docs/07-schedule-releases.md",
        module="scheduler",
        argv=_scheduler,
        preview_flag="--dry-run",
        counts_doc="Reasons carry one entry per release that is due and would not go out.",
    ),
    _release(
        "release.now",
        "Release a scheduled entry now.",
        _RELEASE_ENTRY_ARGS,
    ),
    _release(
        "release.early",
        "Release a planned entry before its scheduled time.",
        _RELEASE_ENTRY_ARGS,
    ),
    _release(
        "release.rerun",
        "Release an entry again, to carry a fixed file.",
        _RELEASE_ENTRY_ARGS,
    ),
    _release(
        "release.adhoc",
        "Release a folder that is not in the schedule.",
        _args(_DEPLOY_FIELDS, required=("course_source_repo", "course_source_path")),
    ),
    Operation(
        name="release.propagate_back",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(),
        help="Keep this cohort's edits for future terms: one pull request per source repo.",
        done_text="Cohort edits checked for future terms.",
        doc="docs/08-release-materials-to-cohort.md",
        module="propagate",
        argv=_course_cohort,
        preview_flag="--dry-run",
        real_flag="--no-dry-run",
    ),
    Operation(
        name="assignment.handout_now",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {
                "course_source_repo": _TEMPLATE,
                "include_solution": _boolean("Also push the solution branch"),
            },
            required=("course_source_repo",),
        ),
        help="Hand out an assignment now.",
        done_text="Assignment handed out.",
        doc="docs/09-release-assignment-to-cohort.md",
        module="assign",
        argv=_handout,
        preview_flag="--dry-run",
        real_flag="--no-dry-run",
        counts_doc="Repos by what happened to them (created, skipped, ...).",
    ),
    Operation(
        name="assignment.update_copies",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {
                "course_source_repo": _TEMPLATE,
                "path": _string(PATH_PATTERN, "File or folder on the template's main"),
                "slug": _SLUG,
                "overwrite": _boolean("Replace a file the student has changed"),
            },
            required=("course_source_repo", "path"),
        ),
        help="Update every copy of an assignment with a fixed file.",
        done_text="Every copy updated.",
        doc="docs/09-release-assignment-to-cohort.md",
        module="assign",
        argv=_patch,
        preview_flag="--dry-run",
        real_flag="--no-dry-run",
    ),
    Operation(
        name="assignment.collect_now",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {"course_source_repo": _TEMPLATE, "slug": _SLUG},
            required=("course_source_repo",),
        ),
        help="Bring the marking sheet up to date with the latest submissions.",
        doc="docs/10-grade-and-return-assignments.md",
        module="collect",
        argv=_collect,
        preview_flag="--dry-run",
        via=f"{VIA_WORKFLOW}collect-submissions.yml",
        inputs=("cohort_org", "course_source_repo", "slug", "dry_run"),
    ),
    Operation(
        name="grades.return",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {
                "notify": _boolean("Email students that there are new marks"),
                "receipt_note": _boolean("Post a note on each receipts thread"),
                "include_feedback": _boolean("Put the feedback text in the email"),
            }
        ),
        help="Return marks and feedback to students.",
        done_text="Marks returned.",
        doc="docs/10-grade-and-return-assignments.md",
        module="grades",
        argv=_grades,
        preview_flag="--dry-run",
        real_flag="--no-dry-run",
        counts_doc="Gradebooks written and emails sent, as distribute reports them.",
    ),
    Operation(
        name="roster.send_codes",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(),
        help="Send new codes to every student who has not joined; old codes stop working.",
        done_text="New codes sent.",
        doc="docs/06-enrol-students-to-cohort.md",
        module="enrol_codes",
        argv=_send_codes,
        preview_flag="--dry-run",
        counts_doc="Codes sent and rows skipped; never an address.",
    ),
    Operation(
        name="site.update",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(),
        help="Update the cohort site now.",
        done_text="Student site updated.",
        doc="docs/11-configure-cohort-site.md",
        module="site",
        argv=_site_sync,
    ),
    Operation(
        name="teams.open_window",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {"assignment": _string(KEY_PATTERN, "The schedule.yml assignments key")},
            required=("assignment",),
        ),
        help="Email every student still without a team for this assignment's open "
        "team-formation window.",
        done_text="Students without a team were emailed.",
        doc="docs/09-release-assignment-to-cohort.md",
        module="team_formation",
        argv=_open_window,
        preview_flag="--dry-run",
        real_flag="--no-dry-run",
        counts_doc="Team-formation emails sent, previewed or held; never an address.",
    ),
    Operation(
        name="access.check",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(),
        help="Check staff and student access against people.yml, the roster and teams.",
        done_text="Staff access checked.",
        doc="docs/05-manage-teaching-team.md",
        module="sync_membership",
        argv=_course_cohort,
        preview_flag="--dry-run",
    ),
    Operation(
        name="cohort.archive",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {"force": _boolean("Archive before the cohort's archive date")}
        ),
        help="Archive the cohort: every repo read-only, nothing deleted.",
        done_text="Cohort archived.",
        doc="docs/10-grade-and-return-assignments.md",
        module="teardown",
        argv=_teardown,
        preview_flag="--dry-run",
        real_flag="--no-dry-run",
    ),
    Operation(
        name="course.publish_website",
        runs_as=DISPATCH,
        scope=COURSE,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {
                "source_repo": _string(
                    REPO_PATTERN, "Materials repo; replaces the live public site"
                ),
                "readings_mode": _enum(READINGS_MODES),
                "include_lectures": _boolean("Publish lecture files"),
            },
            required=("source_repo",),
        ),
        help="Publish the public website from a materials repo.",
        done_text="Public website updated.",
        doc="docs/reference/actions-reference.md",
        module="site",
        argv=_publish,
    ),
    Operation(
        name="assignment.derive_starter",
        runs_as=DISPATCH,
        scope=COURSE,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {"course_source_repo": _TEMPLATE}, required=("course_source_repo",)
        ),
        help="Write the student starter onto main from the solution branch.",
        done_text="Student starter written onto main.",
        doc="docs/03-add-assignment-to-course.md",
        module="derive",
        argv=_derive,
        preview_flag="--dry-run",
        real_flag="--no-dry-run",
    ),
    Operation(
        name="assignment.generate_syllabus",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {"course_source_repo": _string(REPO_PATTERN, "Repo holding the syllabus")},
            required=("course_source_repo",),
        ),
        help="Write the syllabus's session list from the cohort's schedule.",
        done_text="Syllabus session list written.",
        doc="docs/07-schedule-releases.md",
        module="syllabus",
        argv=_syllabus,
        # The CLI previews unless told to write: a preview passes nothing.
        preview_flag="",
        real_flag="--write",
    ),
    Operation(
        name="materials.create",
        runs_as=DISPATCH,
        scope=COURSE,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {
                "tag": _string(TAG_PATTERN, "Term tag, e.g. f2026"),
                "copy_from": _string(REPO_PATTERN, "Materials repo to copy forward"),
                "public_dirs": _enum(PUBLIC_DIRS),
                "public_types": _enum(PUBLIC_TYPES),
            },
            required=("tag",),
        ),
        help="Create a materials repo for a term.",
        done_text="Materials repo created.",
        doc="docs/02-add-materials-to-course.md",
        module="scaffold",
        argv=_new_materials,
        refresh_after=True,
    ),
    Operation(
        name="assignment.create",
        runs_as=DISPATCH,
        scope=COURSE,
        required_team=INSTRUCTORS_TEAM,
        args_schema=_args(
            {
                "name": _string(r"^(?!-)[^\x00-\x1f]{1,200}$", "The assignment's name"),
                "number": _string(r"^[0-9]{1,3}$", "Assignment number"),
                "tag": _string(TAG_PATTERN, "Term tag, e.g. f2026"),
                "copy_from": _string(REPO_PATTERN, "Template to copy forward"),
                "format": _string(FORMATS_PATTERN, "Starter file(s), comma-separated"),
                "type": _enum(ASSIGNMENT_TYPES),
                "team_formation": _enum(TEAM_FORMATIONS),
                "submit_via": _enum(SUBMIT_VIA),
                "visibility": _enum(VISIBILITIES),
                "autograde": _boolean("Seed tests and run them at the cutoff"),
            },
            required=("number", "tag"),
        ),
        help="Create an assignment template.",
        done_text="Assignment template created.",
        doc="docs/03-add-assignment-to-course.md",
        module="scaffold",
        argv=_new_assignment,
        refresh_after=True,
    ),
    Operation(
        name=BOOTSTRAP_OP,
        done_text="Cohort set up.",
        runs_as=DISPATCH,
        scope=COHORT,
        required_team=COURSE_ADMIN_TEAM,
        args_schema=_args(),
        help="Set up an empty cohort org for a new term.",
        doc="docs/04-new-cohort-org.md",
        module="bootstrap_course",
        argv=_bootstrap_cohort,
        refresh_after=True,
    ),
)

REGISTRY: dict[str, Operation] = {op.name: op for op in _OPS}


def refresh_command(request: Request) -> list[str]:
    """The `seed refresh` that ends New materials, New assignment and Bootstrap cohort."""
    return ["refresh", "--course-org", request.course_org]


def public_view(op: Operation) -> dict:
    """What `ops.json` publishes of an op: everything but the callables."""
    return {
        "name": op.name,
        "runs_as": op.runs_as,
        "scope": op.scope,
        "required_team": op.required_team,
        "help": op.help,
        "doc": op.doc,
        "preview": op.preview_flag is not None,
        "via": op.via,
        "counts_doc": op.counts_doc,
        "args_schema": op.args_schema,
    }
