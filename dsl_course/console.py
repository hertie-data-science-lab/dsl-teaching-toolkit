"""Run one Instructor Console request: `python -m dsl_course.console --request JSON`.

The Console workflow's only step that runs toolkit code. It parses and authorises the
request (`ops.request`), runs the op's frozen CLI IN-PROCESS - importing the module and
calling its `main`, with `sys.argv` set to the argv the registry spells - and turns what
happened into a `dsl.outcome/1`: a public annotation and a private record (`ops.outcome`).

First implementation of the outcome: `summary` is the CLI's last `Done ...` line (its JSON
tail, when it has one, becomes `counts`), `conclusion` comes from the exit status and the
preview flag, and each `Decision: <ref> not released: <CODE> <sentence>` line the
scheduler's dry run prints becomes one of `reasons`. CLIs migrate to returning a real
Outcome op by op.

Whatever the target does - `sys.exit`, an argparse refusal, an exception - ends as a
conclusion, never as a traceback in the public log.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone

from . import schedule, status
from .gh_teams import acting_login
from .ghcli import gh
from .log import log_err
from .ops.outcome import Outcome, annotation, write_private
from .ops.registry import (
    REGISTRY,
    Operation,
    Request,
    command,
    refresh_command,
    workflow_inputs,
)
from .ops.request import RequestError, check_access, parse_request

# The ops that release a named schedule entry: the console may send just the entry, and the
# deploy fields are read off the cohort's schedule.yml here.
_ENTRY_OPS = ("release.now", "release.early", "release.rerun")
_DONE_RE = re.compile(r"^\s*(?:\[ok\]\s*)?(Done\b.*)$")
_DECISION_RE = re.compile(r"^\s*Decision: (\S+) not released: ([A-Z][A-Z0-9_]*) (.+)$")

_FALLBACK = {
    "done": "Finished.",
    "previewed": "Preview finished; nothing was changed.",
    "nothing_to_do": "Nothing to do.",
    "failed": "Stopped with a problem; the run log says where.",
}


class _Tee:
    """stdout that is both printed (the run log stays what it is today) and kept."""

    def __init__(self, out) -> None:
        self.out = out
        self.lines: list[str] = []
        self._part = ""

    def write(self, text: str) -> int:
        self.out.write(text)
        self._part += text
        *done, self._part = self._part.split("\n")
        self.lines += done
        return len(text)

    def flush(self) -> None:
        self.out.flush()

    def captured(self) -> list[str]:
        return self.lines + ([self._part] if self._part else [])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> int | None:
    raw = os.environ.get("GITHUB_RUN_ID", "")
    return int(raw) if raw.isdigit() else None


def run_cli(module: str, argv: list[str]) -> tuple[int, list[str], bool]:
    """Run `python -m dsl_course.<module> <argv>` in this process: (exit code, stdout
    lines, whether it CRASHED).

    `main()` reads `sys.argv`, as every frozen CLI does, so it is set for the call and put
    back after. A `SystemExit` is the CLI's own exit code. Any other exception is a crash:
    logged by TYPE only (its text may name somebody's repo), never as a traceback."""
    target = importlib.import_module(f"dsl_course.{module}")
    tee = _Tee(sys.stdout)
    saved = sys.argv
    sys.argv = [f"dsl_course.{module}", *argv]
    try:
        with contextlib.redirect_stdout(tee):
            rc = target.main()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:
        log_err(f"{module} stopped on an unexpected {type(exc).__name__}.")
        return 1, tee.captured(), True
    finally:
        sys.argv = saved
    return int(rc or 0), tee.captured(), False


def entry_requests(request: Request) -> list[Request]:
    """One request per source repo for a named schedule entry, the deploy fields filled in
    from the cohort's schedule.yml. A request that already carries them passes through.
    Raises `RequestError` for an entry the schedule does not have."""
    if request.op not in _ENTRY_OPS or request.args.get("course_source_repo"):
        return [request]
    entry = request.args["entry"]
    sched = schedule.load(request.cohort_org)
    found = next((r for r in sched.releases if r.label == entry), None)
    if found is None:
        raise RequestError(
            "ENTRY_NOT_FOUND", f"{entry} is not an entry in this cohort's schedule.yml."
        )
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for d in found.deploy:
        key = (d.course_source_repo, d.cohort_dest_repo or "materials")
        dest = d.cohort_dest_path or d.course_source_path
        groups.setdefault(key, []).append((d.course_source_path, dest))
    return [
        replace(
            request,
            args={
                "entry": entry,
                "course_source_repo": repo,
                "course_source_path": ",".join(src for src, _ in pairs),
                "cohort_dest_repo": dest_repo,
                "cohort_dest_path": ",".join(dst for _, dst in pairs),
            },
        )
        for (repo, dest_repo), pairs in groups.items()
    ]


def _summary_and_counts(lines: list[str]) -> tuple[str, dict]:
    done = [m.group(1) for line in lines if (m := _DONE_RE.match(line))]
    if not done:
        return "", {}
    last = done[-1]
    counts: dict = {}
    tail = last.split(" - ", 1)[1] if " - " in last else ""
    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(tail) if tail.startswith("{") else None
        if isinstance(parsed, dict):
            counts = {k: v for k, v in parsed.items() if isinstance(v, int)}
    return last, counts


def _reasons(lines: list[str]) -> list[dict]:
    return [
        {"code": m.group(2), "text": f"{m.group(1)} not released: {m.group(3)}"}
        for line in lines
        if (m := _DECISION_RE.match(line))
    ]


class Broken(RuntimeError):
    """The run itself broke - no token, an unexpected exception, a record that could not
    be written. The one case the CLI exits non-zero for: that is what files the
    "Console is failing" issue and mails the maintainer, so a refusal must never be it."""


def execute(op: Operation, requests: list[Request]) -> tuple[int, list[str], bool]:
    """Run the op once per request, then the refresh its workflow ends in. Stops at the
    first failure, as the workflow's `bash -e` step would."""
    rc, lines, crashed = 0, [], False
    for req in requests:
        rc, out, crashed = run_cli(op.module, command(op, req))
        lines += out
        if rc:
            return rc, lines, crashed
    if op.refresh_after and requests and not requests[0].preview:
        rc, out, crashed = run_cli("seed", refresh_command(requests[0]))
        lines += out
    return rc, lines, crashed


def dispatch(op: Operation, request: Request) -> tuple[bool, str]:
    """Start a `workflow:` op's own workflow in the course org's `.github`: (started, the
    run's URL or GitHub's refusal). The inputs are what the op's argv maps to."""
    inputs = workflow_inputs(op, command(op, request))
    fields = ["-f", "ref=main", "-F", "return_run_details=true"]
    for name, value in inputs.items():
        fields += ["-f", f"inputs[{name}]={value}"]
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{request.course_org}/.github/actions/workflows/{op.workflow}/dispatches",
        *fields,
    )
    if code != 0:
        return False, out[:200]
    with contextlib.suppress(json.JSONDecodeError):
        details = json.loads(out) if out else {}
        if isinstance(details, dict):
            return True, str(details.get("html_url") or details.get("run_url") or "")
    return True, ""


def identity_refusal(request: Request, login: str) -> str | None:
    """None when the request speaks for whoever is actually running it, else why not.

    Nothing in the request is trusted: inside Actions the actor must be `$GITHUB_ACTOR`
    and the course org the repository's owner; on a laptop, the login `gh` is using."""
    if os.environ.get("GITHUB_ACTIONS"):
        actor = os.environ.get("GITHUB_ACTOR", "")
        owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
        if request.actor.casefold() != actor.casefold():
            return f"The request names @{request.actor}, but @{actor} started this run."
        if request.course_org.casefold() != owner.casefold():
            return (
                f"The request names {request.course_org}, but this run is in {owner}."
            )
        return None
    if request.actor.casefold() != login.casefold():
        return f"The request names @{request.actor}, but gh is signed in as @{login}."
    return None


def _refuse(code: str, text: str, raw: dict, started: str) -> Outcome:
    return Outcome(
        op=str(raw.get("op", "")),
        actor=str(raw.get("actor", "")),
        preview=bool(raw.get("preview", False)),
        conclusion="failed",
        summary=text,
        run_id=_run_id(),
        reasons=[{"code": code, "text": text}],
        started=started,
        finished=_now(),
    )


def _loose(text: str) -> dict:
    """The request's fields as far as they parse, for the record of a refused one."""
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        raw = json.loads(text)
        if isinstance(raw, dict):
            return raw
    return {}


def _finish(outcome: Outcome, request: Request | None) -> None:
    """Emit the annotation, then - for a request that got past its identity check - the
    private record and the status refresh. A record that cannot be written is a broken
    run; the status refresh is best-effort."""
    print(annotation(outcome), flush=True)
    if request is None:
        return
    if not write_private(outcome, request.cohort_org, request.course_org):
        raise Broken("the outcome could not be recorded")
    # WP2's status writer. Until it lands there is nothing to call.
    hook = getattr(status, "write_after_op", None)
    if callable(hook):
        try:
            hook(request)
        except Exception as exc:
            log_err(f"could not refresh status.json: {type(exc).__name__}.")


def _outcome(op: Operation, request: Request, started: str) -> tuple[Outcome, bool]:
    """Run the op and say what happened, and whether the target crashed."""
    if op.workflow:
        started_ok, detail = dispatch(op, request)
        if not started_ok:
            text = f"{op.workflow} could not be started: {detail}"
            return _refuse("DISPATCH_FAILED", text, vars(request), started), False
        return Outcome(
            op=op.name,
            actor=request.actor,
            preview=request.preview,
            conclusion="done",
            summary=f"Started {op.workflow}: {detail}"
            if detail
            else f"Started {op.workflow}.",
            run_id=_run_id(),
            started=started,
            finished=_now(),
        ), False
    requests = entry_requests(request)
    crashed = False
    if not requests:
        conclusion, lines = "nothing_to_do", []
        summary = f"{request.args.get('entry')} has nothing to release."
    else:
        rc, lines, crashed = execute(op, requests)
        conclusion = "failed" if rc else ("previewed" if request.preview else "done")
        summary = ""
    done, counts = _summary_and_counts(lines)
    return Outcome(
        op=op.name,
        actor=request.actor,
        preview=request.preview,
        conclusion=conclusion,
        summary=summary or done or _FALLBACK[conclusion],
        run_id=_run_id(),
        counts=counts,
        reasons=_reasons(lines),
        started=started,
        finished=_now(),
    ), crashed


def run(text: str) -> int:
    """0 whenever an Outcome was produced - refusals and content failures included - and
    1 when the run itself broke: no token, or a target that crashed."""
    started = _now()
    try:
        request = parse_request(text)
    except RequestError as exc:
        _finish(_refuse(exc.code, exc.text, _loose(text), started), None)
        return 0
    login = acting_login()
    if login is None:
        raise Broken("gh is not authenticated (empty or invalid GH_TOKEN?)")
    raw = vars(request)
    refusal = identity_refusal(request, login)
    if refusal:
        # Not recorded privately: the request's orgs are exactly what is in doubt.
        _finish(_refuse("ACTOR_MISMATCH", refusal, raw, started), None)
        return 0
    refusal = check_access(request)
    if refusal:
        _finish(_refuse("NOT_ALLOWED", refusal, raw, started), request)
        return 0
    try:
        outcome, crashed = _outcome(REGISTRY[request.op], request, started)
    except RequestError as exc:
        outcome, crashed = _refuse(exc.code, exc.text, raw, started), False
    _finish(outcome, request)
    return 1 if crashed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request", required=True, help="A dsl.request/1 JSON document"
    )
    args = parser.parse_args()
    try:
        return run(args.request)
    except Broken as exc:
        log_err(f"The Console run broke: {exc}.")
        return 1
    except Exception as exc:
        log_err(f"The Console run broke on an unexpected {type(exc).__name__}.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
