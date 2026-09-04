"""Adding and removing this run's assignment in a cohort's `schedule.yml`.

The harness has to put a real assignment into a real cohort's schedule and take it out
again, in a file faculty own and hand-edit. So the edit is FENCED - `# dsl-e2e:<run> begin`
/ `# dsl-e2e:<run> end` - rather than parsed and re-emitted: a YAML round trip would
reformat and de-comment the whole file, and an interrupted run would leave the cohort's
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
        return "\n".join(lines) + "\n"
    where = next(
        (i for i, line in enumerate(lines) if line.rstrip() == _ASSIGNMENTS), None
    )
    if where is None:
        raise ValueError(
            f"this schedule.yml has no `{_ASSIGNMENTS}` key to put the run's assignment "
            "under - the cohort is not set up for the e2e pipeline"
        )
    lines[where + 1 : where + 1] = fenced.splitlines()
    return "\n".join(lines) + "\n"


def remove_block(text: str, run_id: str) -> str:
    """`text` with this run's fenced block gone. Unchanged when there is none, so cleanup
    is re-runnable and a partial run leaves nothing to reason about."""
    span = _fences(text, run_id)
    if span is None:
        return text
    begin, end = span
    lines = text.splitlines()
    del lines[begin : end + 1]
    return "\n".join(lines) + "\n"


def put_schedule(cohort: str, text: str, sha: str) -> bool:
    """Write the edited schedule back, refusing if it moved since it was read.

    `expected_sha` is the whole point: the seeded workflows write this file too (the
    scheduler records handouts in it), so a blind write could revert a commit that landed
    between the read and the edit."""
    return gh_contents.put_file(
        cohort,
        course.CONFIG_REPO,
        schedule.SCHEDULE_PATH,
        text.encode(),
        "e2e: fenced test assignment",
        expected_sha=sha,
    )
