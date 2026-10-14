"""`python -m dsl_course` - every Instructor Console operation, from a laptop.

    python -m dsl_course                         list every operation
    python -m dsl_course <op> --help             what it does and what it takes
    python -m dsl_course <op> --course-org C [--semester-org S] [--arg key=value ...]

The parity contract (decision 0041): each op in `ops.registry` runs here with the inputs
the Console sends, as the same `dsl.request/1`, through the same parser (`ops.request`) and
the same in-process runner (`console.handle`), so both surfaces end in one outcome
sentence. `--arg` takes the op's arguments by their Console names; a boolean is `true` or
`false`. Like every CLI it previews unless given `--no-preview`; an op with no preview
runs only when given `--no-preview`. `gh` must be signed in: the request is made as that
account, and its access is checked exactly as the Console's is.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import console
from .gh_teams import acting_login
from .log import CLIParser, add_preview_flag, log, log_err
from .ops.outcome import Outcome
from .ops.registry import REGISTRY, REQUEST_SCHEMA, SEMESTER, Operation

CLIENT = "cli"


def listing() -> list[str]:
    """Every op this CLI runs, in registry order: the list the parity test compares."""
    return list(REGISTRY)


def _arg_lines(op: Operation) -> list[str]:
    schema = op.args_schema
    required = set(schema.get("required", []))
    lines = []
    for name, spec in schema.get("properties", {}).items():
        kind = "/".join(spec["enum"]) if "enum" in spec else spec.get("type", "")
        need = "required" if name in required else "optional"
        lines.append(f"  {name} ({kind}, {need}): {spec.get('description', '')}")
    return lines


def _usage() -> str:
    width = max(len(name) for name in REGISTRY)
    rows = [f"  {op.name:<{width}}  {op.help}" for op in REGISTRY.values()]
    return "\n".join(["Operations (python -m dsl_course <op> --help for one):", *rows])


def _parser(op: Operation) -> CLIParser:
    args = _arg_lines(op)
    parser = CLIParser(
        prog=f"python -m dsl_course {op.name}",
        description=op.help,
        epilog="\n".join(["--arg names:", *args]) if args else "Takes no --arg.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--course-org", required=True)
    if op.scope == SEMESTER:
        parser.add_argument("--semester-org", required=True)
    parser.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="One of the op's arguments, as the Console names it; repeatable",
    )
    add_preview_flag(parser, "Preview only (the default); --no-preview runs it")
    return parser


def _value(op: Operation, key: str, raw: str) -> object:
    """The JSON value a `--arg` stands for: booleans by the op's schema, else the text.
    An unknown key is passed through, for the request parser to refuse by name."""
    spec = op.args_schema.get("properties", {}).get(key, {})
    if spec.get("type") == "boolean":
        if raw.lower() not in ("true", "false"):
            raise ValueError(f"--arg {key} takes true or false, not {raw!r}")
        return raw.lower() == "true"
    return raw


def request_text(
    op: Operation,
    actor: str,
    course_org: str,
    semester_org: str | None,
    pairs: list[str],
    preview: bool,
) -> str:
    """The `dsl.request/1` the Console would send for these inputs."""
    args = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--arg takes KEY=VALUE, not {pair!r}")
        args[key] = _value(op, key, raw)
    request = {
        "schema": REQUEST_SCHEMA,
        "op": op.name,
        "actor": actor,
        "course_org": course_org,
        "args": args,
        "preview": preview,
        "client": CLIENT,
    }
    if semester_org:
        request["semester_org"] = semester_org
    return json.dumps(request)


def _say(outcome: Outcome) -> None:
    log(f"{outcome.conclusion}: {outcome.summary}")
    for reason in outcome.reasons:
        log(f"  {reason.get('code', '')}: {reason.get('text', '')}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "list"):
        print(_usage())
        return 0
    op = REGISTRY.get(argv[0])
    if op is None:
        log_err(f"{argv[0]} is not an operation. {_usage()}")
        return 2
    args = _parser(op).parse_args(argv[1:])
    if args.preview and op.preview_flag is None:
        log_err(f"{op.name} has no preview: run it with --no-preview.")
        return 2
    actor = acting_login()
    if actor is None:
        log_err("gh is not signed in (gh auth login, or GH_TOKEN).")
        return 1
    try:
        text = request_text(
            op,
            actor,
            args.course_org,
            getattr(args, "semester_org", None),
            args.arg,
            args.preview,
        )
    except ValueError as exc:
        log_err(str(exc))
        return 2
    return console.handle(text, _say)


if __name__ == "__main__":
    sys.exit(main())
