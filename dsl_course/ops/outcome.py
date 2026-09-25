"""The `dsl.outcome/1` every Console run emits, twice.

PUBLIC: one `::notice title=dsl-outcome::<json>` annotation, which the console reads off the
run. The Console workflow runs in the course org's public `.github`, so this half carries no
`people` and no repo name of the `<slug>-<handle>` / `grades-<handle>` form - the same rule
`log.log_person` keeps for every other line of a faculty workflow's log.

PRIVATE: `semester-config/.system/outcomes/<op>.json` in the semester org, which may carry the
per-person lines. A course-wide op has no private repo to write to, so its file goes to the
course org's `.github/.system/outcomes/<op>.json` in the public form.
"""

from __future__ import annotations

import json
import re
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
    people: list[dict] = field(default_factory=list)
    started: str = ""
    finished: str = ""

    def to_dict(self) -> dict:
        return {"schema": OUTCOME_SCHEMA, **asdict(self)}


def redact(text: str, handles: set[str] = frozenset()) -> str:
    """`text` with every person-naming repo name and every known handle replaced."""
    out = _GRADEBOOK_RE.sub(f"{GRADEBOOK_PREFIX}{HANDLE_MARK}", text)
    out = _SUBMISSION_RE.sub(rf"\1-{HANDLE_MARK}", out)
    for handle in sorted(handles, key=len, reverse=True):
        out = re.sub(
            rf"(?<![A-Za-z0-9-]){re.escape(handle)}(?![A-Za-z0-9-])",
            HANDLE_MARK,
            out,
            flags=re.IGNORECASE,
        )
    return out


def _redacted(value: object, handles: set[str]) -> object:
    if isinstance(value, str):
        return redact(value, handles)
    if isinstance(value, dict):
        return {k: _redacted(v, handles) for k, v in value.items()}
    if isinstance(value, list):
        return [_redacted(v, handles) for v in value]
    return value


def public_dict(outcome: Outcome) -> dict:
    """The outcome as a public surface may show it: no `people`, nothing naming anyone.
    The actor is kept - it is the member of staff who pressed the button."""
    handles = {p["handle"] for p in outcome.people if p.get("handle")}
    data = outcome.to_dict()
    data.pop("people")
    actor = data.pop("actor")
    return {"actor": actor, **_redacted(data, handles)}


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


def annotation(outcome: Outcome) -> str:
    return f"::notice title={ANNOTATION_TITLE}::{_capped(public_dict(outcome))}"


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
