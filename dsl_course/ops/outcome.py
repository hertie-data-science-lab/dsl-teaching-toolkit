"""The `dsl.outcome/1` every Console run emits, twice.

PUBLIC: one `::notice title=dsl-outcome::<json>` annotation, which the console reads off the
run. The Console workflow runs in the course org's public `.github`, so this half carries no
repo name of the `<slug>-<handle>` / `grades-<handle>` form - the same rule `log.log_person`
keeps for every other line of a faculty workflow's log.

PRIVATE: `semester-config/.system/outcomes/<op>.json` in the semester org, unredacted. A
course-wide op has no private repo to write to, so its file goes to the course org's
`.github/.system/outcomes/<op>.json` in the public form.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import asdict, dataclass, field

from .. import records
from ..course import CONFIG_REPO, GRADEBOOK_PREFIX
from ..gh_contents import put_file
from .registry import OUTCOME_SCHEMA

CONCLUSIONS = ("done", "nothing_to_do", "skipped", "previewed", "failed")
OUTCOMES_DIR = records.path("outcomes")
ANNOTATION_TITLE = "dsl-outcome"
HANDLE_MARK = "<handle>"
# Under the Checks API's 64 KB cap on an annotation message, with room to spare.
ANNOTATION_CAP = 60_000
TRUNCATED = "(truncated; the full text is in the outcome file)"

# `grades-<anything>` is always a person's gradebook. `assignment-N-<suffix>` is a
# submission repo unless the suffix is a term tag (the template itself), `submissions`
# (the shared drop box, which names nobody) or already redacted.
_GRADEBOOK_RE = re.compile(
    rf"\b{re.escape(GRADEBOOK_PREFIX)}(?!<)[A-Za-z0-9][A-Za-z0-9-]*"
)
_SUBMISSION_RE = re.compile(
    r"\b(assignment-\d+)-(?![fs]\d{4}\b)(?!submissions\b)(?!<)[A-Za-z0-9][A-Za-z0-9-]*"
)


def _named_submission_re(name: str) -> re.Pattern:
    """`<name>-<suffix>` for one assignment of the run's schedule: a key is free
    (decision 0014), so `trees-ada-l` is a submission repo as much as `assignment-3-ada-l`.
    The same exceptions as `_SUBMISSION_RE`."""
    return re.compile(
        rf"(?<![A-Za-z0-9-])({re.escape(name)})-(?![fs]\d{{4}}\b)(?!submissions\b)"
        r"(?!<)[A-Za-z0-9][A-Za-z0-9-]*",
        re.IGNORECASE,
    )


@dataclass
class Outcome:
    op: str
    actor: str
    preview: bool
    conclusion: str
    summary: str
    run_id: int | None = None
    counts: dict = field(default_factory=dict)
    reasons: list[dict] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    block: str = ""
    started: str = ""
    finished: str = ""

    def to_dict(self) -> dict:
        return {"schema": OUTCOME_SCHEMA, **asdict(self)}


def redact(text: str, names: Collection[str] = ()) -> str:
    """`text` with every person-naming repo name replaced. `names` are the assignment names of the run's schedule (its keys and semester-side
    names), each of which a submission repo is named after."""
    out = _GRADEBOOK_RE.sub(f"{GRADEBOOK_PREFIX}{HANDLE_MARK}", text)
    out = _SUBMISSION_RE.sub(rf"\1-{HANDLE_MARK}", out)
    for name in sorted(names, key=len, reverse=True):
        out = _named_submission_re(name).sub(rf"\1-{HANDLE_MARK}", out)
    return out


def _redacted(value: object, names: Collection[str]) -> object:
    if isinstance(value, str):
        return redact(value, names)
    if isinstance(value, dict):
        return {k: _redacted(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_redacted(v, names) for v in value]
    return value


def public_dict(outcome: Outcome, names: Collection[str] = ()) -> dict:
    """The outcome as a public surface may show it: nothing naming anyone. The actor is
    kept - it is the member of staff who pressed the button. `names`: as `redact`."""
    data = outcome.to_dict()
    actor = data.pop("actor")
    return {"actor": actor, **_redacted(data, names)}


def _escape_command(data: str) -> str:
    """GitHub's workflow-command escaping for the message part of `::notice::`."""
    return data.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _message(data: dict) -> str:
    return _escape_command(json.dumps(data, sort_keys=True, separators=(",", ":")))


def _capped(data: dict) -> str:
    """The annotation's message, at most `ANNOTATION_CAP` bytes. The Checks API cuts an
    annotation message at 64 KB, and a cut JSON body does not parse, so an outcome too
    big for it loses the end of its `block` first, then its last `details` lines, and says
    so inside `block`. The outcome file keeps everything."""
    message = _message(data)
    if len(message.encode()) <= ANNOTATION_CAP:
        return message
    block, details = str(data.get("block", "")), list(data.get("details", []))
    while True:
        note = f"{block}\n{TRUNCATED}" if block else TRUNCATED
        message = _message({**data, "block": note, "details": details})
        over = len(message.encode()) - ANNOTATION_CAP
        if over <= 0:
            return message
        if block:
            # Every character is at least one byte of the message, so this always fits
            # or empties the block.
            block = block[: max(0, len(block) - over)]
        elif details:
            details.pop()
        else:
            return message


def annotation(outcome: Outcome, names: Collection[str] = ()) -> str:
    return f"::notice title={ANNOTATION_TITLE}::{_capped(public_dict(outcome, names))}"


def private_path(op: str) -> str:
    return f"{OUTCOMES_DIR}/{op}.json"


def write_private(outcome: Outcome, semester_org: str | None, course_org: str) -> bool:
    """Record the outcome where the console reads it back. A semester op writes the full
    record to its private `semester-config`; a course op writes the public form to the
    course org's `.github`. `put_file` compares blobs, so an identical record is no commit."""
    if semester_org:
        org, repo, data = semester_org, CONFIG_REPO, outcome.to_dict()
    else:
        org, repo, data = course_org, ".github", public_dict(outcome)
    content = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    return put_file(
        org,
        repo,
        private_path(outcome.op),
        content,
        f"Console: record the outcome of {outcome.op}",
        person=bool(semester_org),
    )
