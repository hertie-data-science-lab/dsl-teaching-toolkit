"""Adding and removing this run's assignment in a semester's `schedule.yml`.

The harness has to put a real assignment into a real semester's schedule and take it out
again, in a file faculty own and hand-edit. So the edit is FENCED - `# dsl-e2e:<run> begin`
/ `# dsl-e2e:<run> end` - rather than parsed and re-emitted: a YAML round trip would
reformat and de-comment the whole file, and an interrupted run would leave the semester's
schedule rewritten by a machine. With fences, removal is exact, and anything a human wrote
around the block is untouched to the byte.

Both edits are pure text; `put_schedule` is the only part that talks to GitHub.
"""

from __future__ import annotations

from dsl_course import course, gh_contents, schedule

BEGIN = "# dsl-e2e:{run_id} begin"
END = "# dsl-e2e:{run_id} end"

# The fenced entry goes in as the FIRST item of `assignments:`, not at the end of the
# file: a schedule.yml has other top-level keys after that list, and appending would put
# the entry under whichever one happens to come last.
_ASSIGNMENTS = "assignments:"


def _rejoin(lines: list[str], text: str) -> str:
    """`lines` back into one document, ending exactly as `text` did.

    The edit is fenced so that what faculty wrote is untouched TO THE BYTE, and a forced
    trailing newline broke that promise for a schedule.yml that had none: the run put the
    block in, took it out again, and handed the semester back a file one byte longer than it
    borrowed - which the teardown's fidelity check reads as drift, because a blob sha
    cannot tell a harmless newline from a real edit. Observable only since
    `gh_contents.get_file_content` stopped stripping what it reads."""
    end = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + end


def _fences(text: str, run_id: str) -> tuple[int, int] | None:
    """The line indices of this run's begin/end fences, or None if it has none."""
    lines = text.splitlines()
    begin, end = BEGIN.format(run_id=run_id), END.format(run_id=run_id)
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not starts or not ends:
        return None
    return starts[0], ends[-1]


def insert_block(text: str, run_id: str, block: str) -> str:
    """`text` with `block` fenced under this run's markers, at the top of `assignments:`.

    Idempotent: a block already fenced for this run is REPLACED, so a retried step does
    not stack two copies of the same assignment into one schedule."""
    fenced = "\n".join(
        [BEGIN.format(run_id=run_id), block.rstrip("\n"), END.format(run_id=run_id)]
    )
    span = _fences(text, run_id)
    lines = text.splitlines()
    if span is not None:
        begin, end = span
        lines[begin : end + 1] = fenced.splitlines()
        return _rejoin(lines, text)
    where = next(
        (i for i, line in enumerate(lines) if line.rstrip() == _ASSIGNMENTS), None
    )
    if where is None:
        raise ValueError(
            f"this schedule.yml has no `{_ASSIGNMENTS}` key to put the run's assignment "
            "under - the semester is not set up for the e2e pipeline"
        )
    lines[where + 1 : where + 1] = fenced.splitlines()
    return _rejoin(lines, text)


def remove_block(text: str, run_id: str) -> str:
    """`text` with this run's fenced block gone. Unchanged when there is none, so cleanup
    is re-runnable and a partial run leaves nothing to reason about."""
    span = _fences(text, run_id)
    if span is None:
        return text
    begin, end = span
    lines = text.splitlines()
    del lines[begin : end + 1]
    return _rejoin(lines, text)


def put_schedule(semester: str, text: str, sha: str) -> bool:
    """Write the edited schedule back, refusing if it moved since it was read.

    `expected_sha` is the whole point: the seeded workflows write this file too (the
    scheduler records handouts in it), so a blind write could revert a commit that landed
    between the read and the edit."""
    return gh_contents.put_file(
        semester,
        course.CONFIG_REPO,
        schedule.SCHEDULE_PATH,
        text.encode(),
        "e2e: fenced test assignment",
        expected_sha=sha,
    )
