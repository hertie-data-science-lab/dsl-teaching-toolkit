"""Run one Instructor Console request: `python -m dsl_course.console --request JSON`.

The Console workflow's only step that runs toolkit code. It parses and authorises the
request (`ops.request`), runs the op's frozen CLI IN-PROCESS - importing the module and
calling its `main`, with `sys.argv` set to the argv the registry spells - and turns what
happened into a `dsl.outcome/1`: a public annotation and a private record (`ops.outcome`).

The outcome's words come from the CLI itself: a `main` that returns a `log.Summary` (an
exit code carrying one sentence, its counts and its reasons) supplies `summary`, `counts`
and `reasons`; one that returns a bare exit code gets the op's own `done_text`.
`conclusion` comes from the exit status and the preview flag, unless the Summary says a
successful run had nothing to do. Nothing is parsed out of the CLI's stdout.

Whatever the target does - `sys.exit`, an argparse refusal, an exception - ends as a
conclusion, never as a traceback in the public log.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

from . import schedule, status
from .gh_teams import acting_login
from .ghcli import gh
from .log import Summary, log, log_err
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

_REFRESH_FAILED = {
    "code": "REFRESH_FAILED",
    "text": "Done, but the buttons were not all refreshed; the nightly refresh adds them.",
}

# The op that archives the cohort's classroom-config, where its own record would go.
_ARCHIVE_OP = "cohort.archive"

_FALLBACK = {
    "done": "Finished.",
    "previewed": "Preview finished; nothing was changed.",
    "nothing_to_do": "Nothing to do.",
    "skipped": "Passed over; nothing was changed.",
    "failed": "Stopped with a problem; the run log says where.",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> int | None:
    raw = os.environ.get("GITHUB_RUN_ID", "")
    return int(raw) if raw.isdigit() else None


def run_cli(module: str, argv: list[str]) -> tuple[int, Summary | None, bool]:
    """Run `python -m dsl_course.<module> <argv>` in this process: (exit code, the
    `Summary` its `main` returned if it returned one, whether it CRASHED).

    `main()` reads `sys.argv`, as every frozen CLI does, so it is set for the call and put
    back after. A `SystemExit` is the CLI's own exit code. Any other exception is a crash:
    logged by TYPE only (its text may name somebody's repo), never as a traceback."""
    target = importlib.import_module(f"dsl_course.{module}")
    saved = sys.argv
    sys.argv = [f"dsl_course.{module}", *argv]
    try:
        rc = target.main()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:
        log_err(f"{module} stopped on an unexpected {type(exc).__name__}.")
        return 1, None, True
    finally:
        sys.argv = saved
    summary = rc if isinstance(rc, Summary) else None
    return int(rc or 0), summary, False


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


def combine(
    summaries: list[Summary],
) -> tuple[str, dict, list[dict], list[str], str | None]:
    """`(text, counts, reasons, details, conclusion)` of the op, from the Summary of each CLI call
    it made - one, except for a release entry drawn from several source repos. Counts
    add up; different sentences are joined into one; the conclusion override stands
    only when every call agreed on it."""
    texts = list(dict.fromkeys(s.text for s in summaries if s.text))
    if len(texts) > 1:
        rest = [t[:1].lower() + t[1:] for t in texts[1:]]
        text = "; ".join(t.rstrip(".") for t in [texts[0], *rest]) + "."
    else:
        text = texts[0] if texts else ""
    counts: dict[str, int] = {}
    for s in summaries:
        for key, value in s.counts.items():
            if isinstance(value, int) and not isinstance(value, bool):
                counts[key] = counts.get(key, 0) + value
    reasons = [r for s in summaries for r in s.reasons]
    details = [d for s in summaries for d in s.details]
    overrides = {s.conclusion for s in summaries}
    conclusion = overrides.pop() if len(overrides) == 1 else None
    return text, counts, reasons, details, conclusion


class Broken(RuntimeError):
    """The run itself broke - no token, an unexpected exception, a record that could not
    be written. The one case the CLI exits non-zero for: that is what files the
    "Console is failing" issue and mails the maintainer, so a refusal must never be it."""


def execute(op: Operation, requests: list[Request]) -> tuple[int, list[Summary], bool]:
    """Run the op once per request, then the refresh its workflow ends in. Stops at the
    first failure, as the workflow's `bash -e` step would. The refresh's own summary is
    not the op's, and neither is its exit code: a refresh that failed is a reason."""
    rc, summaries, crashed = 0, [], False
    for req in requests:
        rc, summary, crashed = run_cli(op.module, command(op, req))
        summaries += [summary] if summary is not None else []
        if rc:
            return rc, summaries, crashed
    if op.refresh_after and requests and not requests[0].preview:
        # The op has done its work; a refresh that did not finish is a reason on it, not
        # its failure - reporting a made repo as not made invites a colliding retry.
        refresh_rc, _summary, crashed = run_cli("seed", refresh_command(requests[0]))
        if refresh_rc:
            summaries.append(Summary("", reasons=[_REFRESH_FAILED]))
    return rc, summaries, crashed


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


def _sealed(outcome: Outcome, request: Request) -> bool:
    """Whether the op has just made the private record unwritable: a real archive seals
    `classroom-config` as its last step, so there is nowhere left to write to."""
    return (
        request.op == _ARCHIVE_OP
        and not request.preview
        and outcome.conclusion in ("done", "nothing_to_do")
    )


def _finish(outcome: Outcome, request: Request | None) -> None:
    """Emit the annotation, then - for a request that got past its identity check - the
    private record and the status refresh. A record that cannot be written is a broken
    run; the status refresh is best-effort."""
    print(annotation(outcome), flush=True)
    if request is None:
        return
    if _sealed(outcome, request):
        log("  the cohort is archived now, so the annotation is its only record")
    elif not write_private(outcome, request.cohort_org, request.course_org):
        raise Broken("the outcome could not be recorded")
    status.write_after_op(vars(request))


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
            conclusion="previewed" if request.preview else "done",
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
        conclusion, summaries = "nothing_to_do", []
        summary = f"{request.args.get('entry')} has nothing to release."
    else:
        rc, summaries, crashed = execute(op, requests)
        conclusion = "failed" if rc else ("previewed" if request.preview else "done")
        summary = ""
    text, counts, reasons, details, override = combine(summaries)
    if conclusion == "done" and override:
        conclusion = override
    fallback = op.done_text if conclusion == "done" and op.done_text else None
    return Outcome(
        op=op.name,
        actor=request.actor,
        preview=request.preview,
        conclusion=conclusion,
        summary=summary or text or fallback or _FALLBACK[conclusion],
        run_id=_run_id(),
        counts=counts,
        reasons=reasons,
        details=details,
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
        # Not recorded privately either: the refusal may be that the cohort is not this
        # course's, and a refused caller must not be able to write into any org.
        _finish(_refuse("NOT_ALLOWED", refusal, raw, started), None)
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
