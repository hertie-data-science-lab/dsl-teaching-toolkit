"""migrate - convert one live org to the names and layout of decisions 0010 and 0012.

The engine knows only the new names: an old one is a hard NOT_MIGRATED fault. This is the
one module that knows the OLD ones, and the one thing that moves an org from them. It runs
from a maintainer's laptop with the maintainer's token, never from a workflow, so a run is
one person's deliberate act; the maintainer runs it from the toolkit checkout that the
org's `central_ref` will run once it is migrated.

    python3 -m dsl_course.migrate <org>                 # preview: the plan, nothing written
    python3 -m dsl_course.migrate <org> --no-preview    # do it, step by step

A course org is migrated before any of its semesters. The org's tier comes from its
`.github` topic. Every step says what it will do, does it, verifies it, and stops on the
first failed verification naming that step's rollback. Every step is idempotent: a step
whose work is already done says "already migrated", so a second run changes nothing and a
run that stopped part-way resumes where it stopped. An archived semester is never touched.

The pause is GitHub's own switch: Actions are DISABLED on every repo of the org that runs
workflows, for the window, and enabled again at the end. (An org variable cannot do this:
on GitHub Free org variables do not reach private repos, and the workflows already live in
an org carry no gate until they are re-rendered.)

Semester org, in order: preflight, pause, rename repos, layout, keys, topic, re-render,
status, unpause. Course org: preflight, pause, registry, .system/, dsl-course.yml keys,
template keys, materials files, re-render, status, unpause.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

from . import policy, records, schedule, seed, status
from .bootstrap_course import semester_scaffold
from .central import CENTRAL
from .course import (
    CONFIG_REPO,
    COURSE_CONFIG,
    COURSE_HUB_TOPIC,
    INSTRUCTORS_FILE,
    JOIN_REPO,
    MAINTAINING_FILE,
    MATERIALS_REPO_PREFIX,
    OLD_CONFIG_REPO,
    OLD_JOIN_REPO,
    OLD_PEOPLE_FILE,
    OLD_SEMESTER_TOPIC,
    RETIRED_COURSE_KEYS,
    SEMESTER_TOPIC,
    SOLUTION_BRANCH,
    SYLLABUS_SAMPLE_FILE,
    SYLLABUS_SESSIONS_FILE,
    pages_repo,
)
from .discovery import (
    OLD_SEMESTERS_PATH,
    SEMESTERS_PATH,
    central_ref_for,
    discover_assignment_repos,
    discover_content_repos,
    discover_semesters,
    list_org_repos,
)
from .faults import NOT_MIGRATED, NotMigrated
from .gh_contents import (
    blob_sha,
    get_file_content,
    load_yaml_lines,
    move_files,
    refuse_clashes,
    repo_blob_shas,
)
from .ghcli import gh, git
from .grades import (
    GRADING_FILE,
    TEAM_LOCK_PATH,
    parse_grading_spec,
    sync_team_lock,
    team_lock_content,
)
from .log import CLIParser, add_preview_flag, log, log_err, log_ok, log_step
from .profile_readme import profile_files, update_profile_readme
from .repos import default_branch, repo_missing, set_repo_topics
from .scaffold import materials_system_files
from .setting_readers import RENAMED_SETTINGS
from .settings import ASSIGNMENT_DEFAULTS_KEY
from .sync_faculty import retired_course_faults
from .welcome import (
    config_system_files,
    join_files,
    refresh_config_system_files,
    refresh_join_workflows,
    refresh_semester_pointer,
    template,
)
from .workflows_place import (
    RELEASE_WORKFLOWS,
    RETIRED_WORKFLOWS,
    TEMPLATE_WORKFLOWS,
    content_workflow_files,
)

# ------------------------------------------------------------------ the old layout
# Where each record lived before decision 0010, in semester-config: a path, or a folder
# (trailing `/`) whose every file moves under the new folder. MOVED, never copied: the
# fire-once markers (`autograde/<slug>/_graded.json`, `_skipped.json`,
# `solutions/<slug>.json`, `gradebook/distributed.csv`) must exist exactly once, or the
# next tick re-grades, re-pushes a solution or re-sends marks.
SEMESTER_MOVES = {
    "assignments.lock.yml": records.path("lock"),
    "cohort-gradebook.csv": records.path("semester_gradebook"),
    "archive/teardown.md": records.path("archive"),
    "snapshots/": f"{records.path('snapshots')}/",
    "autograde/": f"{records.path('autograde')}/",
    "solutions/": f"{records.path('solutions')}/",
    "gradebook/": f"{records.path('gradebook')}/",
    "team-formation/": f"{records.path('team_formation')}/",
    ".dsl/": f"{records.SYSTEM_DIR}/",
}
# The course org's `.github`.
COURSE_MOVES = {
    ".dsl/": f"{records.SYSTEM_DIR}/",
    ".github/.last-refresh": records.path("heartbeat"),
    ".github/.missing-cohorts": records.path("missing_semesters"),
}
# A materials repo's root, before its system files moved under `.system/`.
MATERIALS_MOVES = {
    "MAINTAINING.md": MAINTAINING_FILE,
    "SYLLABUS.md.sample": SYLLABUS_SAMPLE_FILE,
    "SYLLABUS.sessions.md": SYLLABUS_SESSIONS_FILE,
}
# The semester's pointer to its course org, before it moved into semester-config.
OLD_POINTER_REPO = ".github"
REPO_RENAMES = {OLD_CONFIG_REPO: CONFIG_REPO, OLD_JOIN_REPO: JOIN_REPO}
SAMPLE_SUFFIX = ".sample"
WORKFLOWS_DIR = ".github/workflows/"
LAYOUT_COMMIT = "migrate: layout"
KEYS_COMMIT = "migrate: keys"


def fold(live: set[str], table: dict[str, str]) -> dict[str, str]:
    """`{old path: new path}` for every file in `live` that `table` moves."""
    out = {}
    for path in sorted(live):
        for old, new in table.items():
            if old.endswith("/") and path.startswith(old):
                out[path] = new + path[len(old) :]
            elif path == old:
                out[path] = new
    return out


def move_lines(moves: dict[str, str], table: dict[str, str]) -> list[str]:
    """The plan's lines for `moves` (from `fold(..., table)`): one per entry of `table` it
    uses - a file as `old -> new`, a folder as `old -> new (N file(s))`. Never the files
    inside a folder: a record's name can carry a person's handle."""
    out = []
    for old, new in table.items():
        if old.endswith("/"):
            count = sum(path.startswith(old) for path in moves)
            if count:
                out.append(f"  {old} -> {new} ({count} file(s))")
        elif old in moves:
            out.append(f"  {old} -> {new}")
    return out


# ------------------------------------------------------------------ key rewrites
# Line rewrites, not YAML round trips: these are instructor files, and their comments and
# layout are theirs. Each returns the text unchanged when there is nothing to rewrite.

_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):")
# A key whose value is a block scalar (`details: >-`, `- notes: |`): group 1 runs up to the
# key, so its length is the key's column; every line indented deeper is the scalar's text.
_BLOCK_SCALAR = re.compile(r"^(\s*(?:-\s+)?)[^\s#:][^:]*:\s*[|>][-+0-9]*\s*(?:#.*)?$")


def split_comment(text: str) -> tuple[str, str]:
    """`(value, tail)`: `tail` is the trailing ` # comment` with the spaces before it,
    verbatim, or "". A `#` inside quotes, or not preceded by a space, is part of the
    value (YAML's own rule)."""
    quote = ""
    for i, char in enumerate(text):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and (i == 0 or text[i - 1] in " \t"):
            value = text[:i].rstrip()
            return value, text[len(value) :]
    return text.rstrip(), ""


def schedule_keys(text: str) -> str:
    """`schedule.yml`: `cohort_dest_*` -> `semester_dest_*` anywhere, and `type:` ->
    `kind:` on the entries of `releases:` and `events:` (an assignment's `type:` is its
    individual/group shape and stays)."""
    out, section = [], ""
    scalar: int | None = None  # the column of the key whose block scalar this is in
    for line in text.split("\n"):
        if scalar is not None and (not line.strip() or _indent(line) > scalar):
            out.append(line)  # prose, however it reads
            continue
        scalar = None
        if top := _TOP_KEY.match(line):
            section = top.group(1)
        line = re.sub(
            r"^(\s*(?:-\s+)?)cohort_dest_(repo|path):", r"\1semester_dest_\2:", line
        )
        if section in ("releases", "events"):
            line = re.sub(r"^(\s+(?:-\s+)?)type:", r"\1kind:", line)
        if block := _BLOCK_SCALAR.match(line):
            scalar = len(block.group(1))
        out.append(line)
    return "\n".join(out)


def formats_line(line: str, indent: str = "") -> str:
    """One `format: x` line (at `indent`) as `formats: [x]`; any other line unchanged.
    An empty `format:` becomes `formats: []`; a list value is kept as it is written."""
    match = re.match(rf"^{indent}format:(.*)$", line)
    if not match:
        return line
    value, tail = split_comment(match.group(1))
    value = value.strip()
    try:
        parsed = yaml.safe_load(value) if value else None
    except yaml.YAMLError:
        return line  # left for the verify step to refuse
    if parsed in (None, ""):
        listed = "[]"
    elif isinstance(parsed, list):
        listed = value
    else:
        listed = f"[{value}]"
    return f"{indent}formats: {listed}{tail}"


def grading_config_keys(text: str) -> str:
    """A template's `grading_config.yml`: the top-level `format:` becomes `formats:`."""
    return "\n".join(formats_line(line) for line in text.split("\n"))


def course_config_keys(text: str) -> str:
    """The course `dsl-course.yml`: every retired key (`course.RETIRED_COURSE_KEYS` -
    `org`, `org_name`, `cohort_defaults`, `semester_defaults`) removed with its block, and
    the `format:` under `assignment_defaults:` -> `formats:`. `assignment_defaults` stays:
    it is the course layer of the cascade (decision 0009)."""
    out, section = [], ""
    for line in text.split("\n"):
        if top := _TOP_KEY.match(line):
            section = top.group(1)
        if section in RETIRED_COURSE_KEYS and (top or line[:1] in (" ", "\t")):
            continue  # the key's own line, or a line of its block
        if section in RETIRED_COURSE_KEYS and line.strip():
            section = ""  # a top-level comment ends the block
        if section == ASSIGNMENT_DEFAULTS_KEY and not top:
            indent = re.match(r"^(\s+)format:", line)
            if indent:
                line = formats_line(line, indent.group(1))
        out.append(line)
    return "\n".join(out)


# The course's old per-semester defaults: stripped by `course_config_keys`. Their two
# settings now live in each semester's `schedule.yml`, with the policy's as the default.
OLD_SEMESTER_BLOCKS = ("cohort_defaults", "semester_defaults")


def lost_semester_values(meta: dict, defaults: dict) -> list[str]:
    """`block.key: value (policy: default)` for each `timezone` / `archive.grace_days` of
    an old semester-defaults block in `meta` (a parsed `dsl-course.yml`) that differs from
    the policy's `defaults`: what stripping the block would lose."""
    out = []
    for block in OLD_SEMESTER_BLOCKS:
        raw = meta.get(block)
        if not isinstance(raw, dict):
            continue
        archive = raw.get("archive")
        found = {
            "timezone": (raw.get("timezone"), defaults["timezone"]),
            "archive.grace_days": (
                archive.get("grace_days") if isinstance(archive, dict) else None,
                defaults["archive"]["grace_days"],
            ),
        }
        out += [
            f"{block}.{key}: {value} (policy: {want})"
            for key, (value, want) in found.items()
            if value is not None and str(value).strip() != str(want)
        ]
    return out


def registry_keys(text: str) -> str:
    """The course registry: its `cohorts:` key becomes `semesters:`."""
    return re.sub(r"(?m)^cohorts:", "semesters:", text)


_ROLE_KEYS = {"instructors": "instructor", "teaching_assistants": "teaching_assistant"}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _flow(entry: dict, role: str) -> str:
    """A flow-style entry with `role:` after its first key, as one line."""
    items = list(entry.items())
    ordered = dict([*items[:1], ("role", role), *items[1:]])
    dumped = yaml.safe_dump(
        ordered, default_flow_style=True, sort_keys=False, width=10**6
    )
    return dumped.strip()


def people_to_instructors(text: str) -> str | None:
    """`people.yml` (a `people:` mapping of role -> list) as `instructors.yml` (one
    `instructors:` list, `role:` on every entry), line by line so comments survive. Any
    indent width, a comment after a role key, and flow-style entries are all read. None
    when the file is not a shape this can convert faithfully - that one goes by hand."""
    lines = text.split("\n")
    head = next((i for i, x in enumerate(lines) if re.match(r"^people:", x)), None)
    if head is None:
        return None
    value, tail = split_comment(lines[head][len("people:") :])
    if value.strip() not in ("", "{}"):
        return None
    body = lines[head + 1 :]
    keys = [x for x in body if x.strip() and not x.lstrip().startswith("#")]
    width = _indent(keys[0]) if keys else 2
    if keys and width == 0:
        return None
    out = [*lines[:head], f"instructors:{tail}" if keys else f"instructors: []{tail}"]
    role, dash = None, None
    for line in body:
        stripped = line.strip()
        if stripped and not line.startswith(" ") and not stripped.startswith("#"):
            return None  # a second top-level key: not a people.yml this can read
        key = re.match(r"^ *([a-z_]+):(.*)$", line)
        if key and _indent(line) == width and not stripped.startswith("#"):
            if key.group(1) not in _ROLE_KEYS:
                return None
            role, dash = _ROLE_KEYS[key.group(1)], None
            value, tail = split_comment(key.group(2))
            if tail:
                out.append(f"  {tail.strip()}")
            if value.strip() in ("", "[]"):
                continue
            try:
                entries = yaml.safe_load(value)
            except yaml.YAMLError:
                return None
            if not isinstance(entries, list) or not all(
                isinstance(e, dict) for e in entries
            ):
                return None
            out += [f"  - {_flow(entry, role)}" for entry in entries]
            continue
        moved = line[width:] if line[:width].strip() == "" else line.lstrip()
        item = re.match(r"^( *)-(?: +(.*))?$", moved)
        if item and not moved.lstrip().startswith("#"):
            if role is None:
                return None
            dash = _indent(moved) if dash is None else dash
            if _indent(moved) == dash:
                rest, tail = split_comment(item.group(2) or "")
                if rest.startswith("{"):
                    try:
                        entry = yaml.safe_load(rest)
                    except yaml.YAMLError:
                        return None
                    if not isinstance(entry, dict):
                        return None
                    out.append(f"{' ' * dash}- {_flow(entry, role)}{tail}")
                    continue
                out.append(moved)
                out.append(f"{' ' * (dash + 2)}role: {role}")
                continue
        out.append(moved)
    return "\n".join(out)


def same_people(old: str, new: str) -> bool:
    """Whether `new` (instructors.yml) names exactly the people of `old` (people.yml),
    field for field, each with the role their old list gave them. Raises yaml.YAMLError
    when either does not parse."""
    before = (yaml.safe_load(old) or {}).get("people") or {}
    if not isinstance(before, dict) or set(before) - set(_ROLE_KEYS):
        return False
    want = [
        {**entry, "role": _ROLE_KEYS[key]}
        for key in _ROLE_KEYS
        for entry in before.get(key) or []
    ]
    got = (yaml.safe_load(new) or {}).get("instructors") or []
    return sorted(want, key=repr) == sorted(got, key=repr)


def _worked_example(ref: str, rel: str) -> str:
    return f"https://github.com/{CENTRAL}/blob/{ref}/example-course/semester-org/{rel}"


def fix_header(text: str, ref: str) -> str:
    """Point the seeded header's links at the worked example, not at the deleted sample
    or the old repo name."""
    example = _worked_example(ref, INSTRUCTORS_FILE)
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            line = line.replace(
                "`people.yml.sample`", f"the worked example ({example})"
            )
            line = re.sub(
                rf"https://github\.com/[^/\s]+/{OLD_CONFIG_REPO}/blob/[^/\s]+/people\.yml",
                example,
                line,
            )
        out.append(line)
    return "\n".join(out)


# ------------------------------------------------------------------ GitHub, narrowly


# `{org: {repo name: listing row}}`: each org is listed once, and listed again only after
# a step has written (`_forget`) - a rename or a topic changes what the listing says.
_LISTINGS: dict[str, dict[str, dict]] = {}


def _listing(org: str) -> dict[str, dict]:
    if org not in _LISTINGS:
        _LISTINGS[org] = {row["name"]: row for row in list_org_repos(org)}
    return _LISTINGS[org]


def _forget() -> None:
    _LISTINGS.clear()


def _topics(listing: dict[str, dict]) -> set[str]:
    return set((listing.get(".github") or {}).get("topics") or [])


def _files(org: str, repo: str, branch: str = "") -> dict[str, str]:
    """`{path: blob sha}` of `repo`, or `{}` when the repo is not there. An absent repo
    (a 404) is "not migrated yet", never an error; any other failure raises."""
    if repo not in _listing(org):
        return {}
    try:
        branch = branch or default_branch(org, repo)
    except RuntimeError:
        if repo_missing(org, repo):
            return {}
        raise
    return repo_blob_shas(org, repo, branch)


def _actions_state(org: str, repo: str) -> dict:
    """`{"enabled": bool, "allowed_actions": str | None}` for `org/repo`. A read that
    fails RAISES: "could not tell" is never "paused" or "unpaused"."""
    code, out = gh("api", f"repos/{org}/{repo}/actions/permissions")
    try:
        data = json.loads(out) if code == 0 else None
    except json.JSONDecodeError:
        data = None
    readable = isinstance(data, dict) and isinstance(data.get("enabled"), bool)
    if not readable:
        raise RuntimeError(
            f"could not read the Actions setting of {org}/{repo}: {out[:200]}"
        )
    return {"enabled": data["enabled"], "allowed_actions": data.get("allowed_actions")}


def _actions_enabled(org: str, repo: str) -> bool:
    return _actions_state(org, repo)["enabled"]


def _set_actions(org: str, repo: str, state: dict) -> bool:
    """Set `org/repo`'s Actions to `state` (`_actions_state`'s shape)."""
    args = ["-F", f"enabled={str(state['enabled']).lower()}"]
    if state["enabled"] and state.get("allowed_actions"):
        args += ["-f", f"allowed_actions={state['allowed_actions']}"]
    code, out = gh(
        "api", "--method", "PUT", f"repos/{org}/{repo}/actions/permissions", *args
    )
    if code != 0:
        log_err(f"could not switch Actions in {org}/{repo}: {out[:200]}")
    return code == 0


def _run_count(org: str, repo: str, query: str, workflow: str = "") -> int:
    """How many runs of `org/repo` (of its `workflow` file only, when named) match
    `query`. Raises when GitHub cannot say."""
    scope = f"workflows/{workflow}/" if workflow else ""
    code, out = gh(
        "api",
        f"repos/{org}/{repo}/actions/{scope}runs?{query}",
        "--jq",
        ".total_count",
    )
    if code != 0 or not out.strip().isdigit():
        raise RuntimeError(f"could not list the runs of {org}/{repo}: {out[:200]}")
    return int(out.strip())


# Every state a run is in before it has finished.
LIVE_RUN_STATES = ("queued", "in_progress", "waiting", "requested", "pending")
# The central toolkit's workflows that re-render the orgs of a tier: one of them running
# while the migration writes is the 422 race (two writers on one branch). All three are
# checked whatever the org's tier: a deploy may have picked its orgs before a tier flip.
CENTRAL_REFRESHERS = ("deploy-main.yml", "deploy-preview.yml", "promote.yml")


def _alive(targets: list[tuple[str, str]]) -> list[str]:
    """The target repos with a run not yet finished, and each central deploy not yet
    finished."""
    out = [
        f"{org}/{repo}"
        for org, repo in targets
        if any(_run_count(org, repo, f"status={s}") for s in LIVE_RUN_STATES)
    ]
    central_org, central_repo = CENTRAL.split("/", 1)
    out += [
        f"{CENTRAL} ({workflow})"
        for workflow in CENTRAL_REFRESHERS
        if any(
            _run_count(central_org, central_repo, f"status={s}", workflow)
            for s in LIVE_RUN_STATES
        )
    ]
    return out


def _rename(org: str, old: str, new: str) -> bool:
    code, out = gh(
        "api", "--method", "PATCH", f"repos/{org}/{old}", "-f", f"name={new}"
    )
    if code != 0:
        log_err(f"could not rename {org}/{old} to {new}: {out[:200]}")
    return code == 0


def _redirects(org: str, old: str, new: str) -> bool:
    """Whether `org/old` answers as `org/new`: GitHub's 301 from a renamed repo's old
    name, which gh follows. What keeps the old URLs in mails and bookmarks working."""
    code, out = gh("api", f"repos/{org}/{old}")
    try:
        name = json.loads(out).get("name") if code == 0 else None
    except (json.JSONDecodeError, AttributeError):
        name = None
    return name == new


def _yaml(text: str | None) -> dict:
    data = yaml.safe_load(text or "") if text else None
    return data if isinstance(data, dict) else {}


def _github_now(org: str) -> str:
    """GitHub's clock, from the `Date` header of a read: the moment the runs' own
    `created` times are compared with, whatever the laptop's clock says. Raises when
    there is no header to read."""
    code, out = gh("api", "--include", f"repos/{org}/.github")
    stamp = next(
        (
            line.split(":", 1)[1].strip()
            for line in out.splitlines()
            if line.lower().startswith("date:")
        ),
        "",
    )
    try:
        when = parsedate_to_datetime(stamp) if code == 0 else None
    except (TypeError, ValueError):
        when = None
    if when is None:
        raise RuntimeError(f"could not read GitHub's clock from {org}/.github")
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _with_workflows(org: str, candidates: list[str]) -> list[tuple[str, str]]:
    """`(org, repo)` for each live candidate repo that carries a workflow."""
    listing = _listing(org)
    return [
        (org, repo)
        for repo in candidates
        if repo in listing
        and not listing[repo].get("archived")
        and any(p.startswith(WORKFLOWS_DIR) for p in _files(org, repo))
    ]


# ------------------------------------------------------------------ steps


@dataclass
class Step:
    name: str
    done: Callable[[], bool]
    plan: Callable[[], list[str]]
    do: Callable[[], bool]
    verify: Callable[[], bool]
    rollback: str
    # "pause" / "unpause" bracket the work. With no work left the pause is skipped; the
    # unpause always asks the repos, so a run that stopped paused is always released.
    bracket: str = ""


def run(org: str, steps: list[Step], preview: bool, pause: Pause) -> int:
    """Print the plan; then (not in preview) do, verify, stop at the first failure.

    From the moment the pause is reached until the unpause is verified, ANY way out - a
    failed step, an error, a Ctrl-C - names the repos whose Actions are still off and
    where their earlier settings are recorded. The names come from what the pause
    recorded, not from a fresh read, so they are there even when GitHub is not."""
    paused = False
    step = steps[0]
    try:
        work = [s for s in steps if not s.bracket]
        idle = all(s.done() for s in work[:-1])  # the last is the status check

        def done(s: Step) -> bool:
            return idle if s.bracket == "pause" else s.done()

        def planned(s: Step) -> bool:
            # A run that pauses unpauses too, whatever the repos say before it starts.
            return idle and s.done() if s.bracket == "unpause" else done(s)

        log_step(f"Migration plan for {org}")
        for step in steps:
            if planned(step):
                log(f"  {step.name}: already migrated")
                continue
            log(f"  {step.name}:")
            for line in step.plan():
                log(f"    - {line}")
        if preview:
            log_ok("PREVIEW - nothing was written. Run again with --no-preview.")
            return 0
        if pause.record() is not None:
            # A run that stopped paused: Actions are off from here on, whatever is left.
            paused = True
            pause.load()
        for step in steps:
            if step.bracket == "pause" and not idle:
                paused = True
                pause.load()
            if done(step):
                log(f"  [skip] {step.name}: already migrated")
                continue
            log_step(step.name)
            done_ok = step.do()
            _forget()
            if not done_ok or not step.verify():
                log_err(
                    f"{step.name} did not verify - stopped here. "
                    f"Rollback: {step.rollback}"
                )
                return 1
            if step.bracket == "unpause":
                paused = False
            log_ok(f"{step.name}: done and verified")
    except Exception as exc:
        log_err(f"{step.name}: stopped by an error - {exc}")
        return 1
    finally:
        # `saved` is filled only once the settings are recorded, the moment before any
        # repo is switched off - so a stop before that says nothing it need not.
        if paused and pause.saved:
            log_err(pause.hint())
    log_ok(f"{org} is migrated")
    return 0


OFF = {"enabled": False, "allowed_actions": None}
PAUSE_RECORD = records.path("migration_pause")
PAUSE_COMMIT = "migrate: record the Actions settings paused"


class Pause:
    """The migration's pause of one org: every repo whose workflows act on it switched
    off, and - in `<org>/.github/.system/migration-pause.json`, written BEFORE anything is
    switched - what each was set to, so the unpause restores exactly that and a run that
    stops (or is interrupted) can always be resumed or released by hand."""

    def __init__(self, org: str, targets: Callable[[], list[tuple[str, str]]]) -> None:
        self.org, self.targets = org, targets
        self.saved: dict[str, dict] = {}  # "org/repo" -> its setting before the pause
        self.started = ""

    def record(self) -> dict[str, dict] | None:
        text = get_file_content(self.org, ".github", PAUSE_RECORD)
        return json.loads(text) if text else None

    def load(self) -> None:
        """Remember the recorded repos, so a stop can name them even if GitHub can't
        be read by then."""
        self.saved = self.record() or self.saved

    @staticmethod
    def _live(key: str) -> tuple[str, str]:
        """A recorded repo under its name now (the rename step may have moved it)."""
        org, repo = key.split("/", 1)
        new = REPO_RENAMES.get(repo)
        if new and repo not in _listing(org) and new in _listing(org):
            return org, new
        return org, repo

    def hint(self) -> str:
        repos = ", ".join(self.saved) or f"(see {self.org}/.github/{PAUSE_RECORD})"
        return (
            f"Actions are still DISABLED in: {repos}. Their settings before the pause "
            f"are in {self.org}/.github/{PAUSE_RECORD}. Re-run the migration with "
            f"--no-preview to finish (or to restore them), or restore each by hand: "
            f"gh api -X PUT repos/<org>/<repo>/actions/permissions -F enabled=true"
        )

    # the pause step ---------------------------------------------------------
    def disabled(self) -> bool:
        """Recorded, and every recorded repo off. An org with no workflow repo records
        `{}`: nothing to switch off is paused."""
        saved = self.record()
        return saved is not None and not any(
            _actions_enabled(*self._live(k)) for k in saved
        )

    def quiet(self, keys: list[str]) -> bool:
        alive = _alive([self._live(k) for k in keys])
        if alive:
            log_err(
                f"a workflow run is queued or running in {', '.join(alive)} - wait "
                f"for it to finish, then re-run the migration"
            )
        return not alive

    def pause(self) -> bool:
        # Read first: a clock that cannot be read stops the pause before anything is
        # written or switched.
        self.started = _github_now(self.org)
        saved = self.record()
        if saved is None:
            saved = {f"{o}/{r}": _actions_state(o, r) for o, r in self.targets()}
            if not move_files(
                self.org,
                ".github",
                {},
                PAUSE_COMMIT,
                files={PAUSE_RECORD: (json.dumps(saved, indent=2) + "\n").encode()},
            ):
                return False
        self.saved = saved
        return all(_set_actions(*self._live(k), OFF) for k in saved)

    def paused(self) -> bool:
        if not self.disabled():
            log_err("Actions did not read back as disabled")
            return False
        # Only a run still going counts: one dispatched in the same second as the pause
        # that has since finished wrote nothing after this run's first write.
        since = f"created=%3E%3D{self.started}"
        late = [
            "/".join(self._live(k))
            for k in self.saved
            if self.started
            and any(
                _run_count(*self._live(k), f"{since}&status={s}")
                for s in LIVE_RUN_STATES
            )
        ]
        if late:
            log_err(
                f"a run started after the pause in {', '.join(late)} is not finished - "
                f"wait for it to finish, then re-run the migration"
            )
            return False
        return self.quiet(list(self.saved))

    # the unpause step --------------------------------------------------------
    def restore(self) -> bool:
        saved = self.record()
        if saved is None:
            return True
        self.saved = saved
        if not all(_set_actions(*self._live(k), state) for k, state in saved.items()):
            return False
        return move_files(self.org, ".github", {}, PAUSE_COMMIT, delete=[PAUSE_RECORD])

    def restored(self) -> bool:
        if self.record() is not None:
            return False
        for key, want in self.saved.items():
            got = _actions_state(*self._live(key))
            if got["enabled"] != want["enabled"] or (
                want["enabled"] and got["allowed_actions"] != want["allowed_actions"]
            ):
                log_err(f"{key}: Actions did not come back as they were ({want})")
                return False
        return True

    def steps(self) -> tuple[Step, Step]:
        names = lambda: ", ".join(f"{o}/{r}" for o, r in self.targets())
        pause = Step(
            "pause automation",
            done=lambda: self.disabled() and self.quiet(list(self.record() or {})),
            plan=lambda: [
                f"record the Actions settings in {self.org}/.github/{PAUSE_RECORD}",
                f"disable Actions in {names()}",
            ],
            do=self.pause,
            verify=self.paused,
            rollback="restore Actions in each repo named below",
            bracket="pause",
        )
        unpause = Step(
            "unpause automation",
            done=lambda: self.record() is None,
            plan=lambda: [f"restore the recorded Actions settings in {names()}"],
            do=self.restore,
            verify=self.restored,
            rollback="restore Actions in each repo by hand (see the line below)",
            bracket="unpause",
        )
        return pause, unpause


def _status_step(course_org: str, semester_org: str | None) -> Step:
    repo = CONFIG_REPO if semester_org else ".github"
    org = semester_org or course_org

    def doc() -> dict:
        text = get_file_content(org, repo, records.path("status"))
        return json.loads(text) if text else {}

    def not_migrated(data: dict) -> int:
        return sum(NOT_MIGRATED in json.dumps(p) for p in data.get("problems") or [])

    def done() -> bool:
        # Written by THIS engine (the old one said `cohort`/`cohorts`) and clean. A
        # status.json the old engine wrote knows nothing of NOT_MIGRATED, so it is
        # never taken as done; one this engine wrote is left alone on a second run.
        data = doc()
        new = (
            "semester" in data
            if semester_org
            else "semesters" in data.get("course", {})
        )
        return bool(data) and new and not_migrated(data) == 0

    def clean() -> bool:
        count = not_migrated(doc())
        if count:
            log_err(f"status.json reports {count} NOT_MIGRATED problem(s) in {org}")
        return count == 0

    return Step(
        "status",
        done=done,
        plan=lambda: [
            f"write {repo}/{records.path('status')} and expect zero NOT_MIGRATED"
        ],
        do=lambda: status.refresh(course_org, semester_org) == 0,
        verify=clean,
        rollback="read the problems in status.json; each names the file to fix",
    )


def _drift(org: str, wanted: dict[str, dict[str, bytes]]) -> list[str]:
    """`repo/path` for every file of `wanted` (`{repo: {path: bytes}}`) that `org` does
    not hold byte for byte."""
    out = []
    for repo, want in wanted.items():
        live = _files(org, repo)
        out += [
            f"{repo}/{p}" for p, body in want.items() if live.get(p) != blob_sha(body)
        ]
    return out


def _no_drift(drift: list[str], *, quiet: bool = False) -> bool:
    """The re-render's verify: nothing left differing, else each file named (unless
    `quiet`)."""
    if drift and not quiet:
        log_err(
            f"{len(drift)} file(s) still differ from this checkout: {', '.join(drift)}"
        )
    return not drift


# A re-render also writes what no file shows (a repo secret, a label, a description), so
# its verify cannot read all of it back. A pause record left by a stopped run means that
# run may have stopped at the re-render: inside the window it is never "already
# migrated", and it runs again - it is idempotent. Outside the window, done IS the verify.
RERUN_NOTE = "run it again: a pause record says the last run stopped inside the window"


def _schedule_clean(text: str | None, *, quiet: bool = False) -> bool:
    """`schedule.yml` read by this engine with no NOT_MIGRATED fault; each one found is
    named (unless `quiet`), for a person to fix by hand."""
    if text is None:
        return True
    sched = schedule.parse(load_yaml_lines(text) or {})
    bad = [f for f in sched.faults if f.code == NOT_MIGRATED]
    bad += [d for d in sched.dropped if NOT_MIGRATED in d]
    for fault in [] if quiet else bad:
        log_err(
            f"{schedule.SCHEDULE_PATH}: {getattr(fault, 'what', fault)} - fix by hand"
        )
    return not bad


def _grading_clean(text: str | None) -> bool:
    """A `grading_config.yml` read by this engine with no NOT_MIGRATED fault."""
    if text is None:
        return True
    try:
        spec = parse_grading_spec(text)
    except NotMigrated:
        return False
    return not any(NOT_MIGRATED in d for d in spec.dropped)


# --------------------------------------------------------------- the semester org


class Semester:
    def __init__(self, org: str, course_org: str) -> None:
        self.org, self.course = org, course_org
        self.people = ""
        self.pause = Pause(org, self.targets)
        self.renamed_now: dict[str, str] = {}  # old -> new, renamed by this run

    def config(self) -> str:
        """The config repo under whichever name it has right now."""
        return CONFIG_REPO if CONFIG_REPO in _listing(self.org) else OLD_CONFIG_REPO

    def join(self) -> str:
        return JOIN_REPO if JOIN_REPO in _listing(self.org) else OLD_JOIN_REPO

    def targets(self) -> list[tuple[str, str]]:
        """Every repo whose workflows act on this semester: its own, and the course's."""
        own = [self.config(), self.join(), pages_repo(self.org), ".github"]
        return _with_workflows(self.org, own) + Course(self.course).targets()

    # rename -------------------------------------------------------------------
    def renames_left(self) -> dict[str, str]:
        names = set(_listing(self.org))
        return {old: new for old, new in REPO_RENAMES.items() if old in names}

    def rename(self) -> bool:
        names = set(_listing(self.org))
        for old, new in self.renames_left().items():
            if new in names:
                log_err(f"{self.org} has both {old} and {new} - resolve by hand")
                return False
            if not _rename(self.org, old, new):
                return False
            self.renamed_now[old] = new
        return True

    def renamed(self) -> bool:
        """No repo left under an old name. A repo that never existed (a semester with
        no `welcome`) has nothing to rename."""
        return not self.renames_left()

    def rename_verified(self) -> bool:
        """Renamed, and each old name this run renamed redirects to its new one."""
        if not self.renamed():
            return False
        stale = [
            o for o, n in self.renamed_now.items() if not _redirects(self.org, o, n)
        ]
        for old in stale:
            log_err(
                f"{self.org}/{old} does not redirect to {self.renamed_now[old]} - old "
                f"links to it are broken"
            )
        return not stale

    # layout -------------------------------------------------------------------
    def instructors_text(self, old: str) -> str | None:
        """The `instructors.yml` for this semester's `people.yml`, or None when it
        cannot be converted faithfully (then a person does it)."""
        ref = central_ref_for(self.course)
        try:
            parsed = yaml.safe_load(old)
        except yaml.YAMLError:
            return None
        if parsed is None:
            # The seeded skeleton, all comments: the current skeleton replaces it.
            return semester_scaffold(self.org, INSTRUCTORS_FILE, ref)
        new = people_to_instructors(old)
        if new is None:
            return None
        new = fix_header(new, ref)
        try:
            return new if same_people(old, new) else None
        except yaml.YAMLError:
            return None

    def layout_work(self) -> tuple[dict, dict, list, bool]:
        """(moves, files, deletes, old pointer present) for semester-config."""
        repo = self.config()
        live = _files(self.org, repo)
        moves = fold(set(live), SEMESTER_MOVES)
        deletes = sorted(p for p in live if p.endswith(SAMPLE_SUFFIX))
        files: dict[str, bytes] = {}
        if OLD_PEOPLE_FILE in live:
            deletes.append(OLD_PEOPLE_FILE)
            if INSTRUCTORS_FILE not in live:
                old = get_file_content(self.org, repo, OLD_PEOPLE_FILE) or ""
                files[INSTRUCTORS_FILE] = (self.instructors_text(old) or "").encode()
        if live and records.path("pointer") not in live:
            files[records.path("pointer")] = self.pointer()
        old_pointer = COURSE_CONFIG in _files(self.org, OLD_POINTER_REPO)
        return moves, files, deletes, old_pointer

    def pointer(self) -> bytes:
        return template("semester/dsl-course.yml").format(course=self.course).encode()

    def layout_done(self) -> bool:
        if not self.renamed():
            return False
        moves, files, deletes, old_pointer = self.layout_work()
        return not (moves or files or deletes or old_pointer)

    def layout_plan(self) -> list[str]:
        moves, files, deletes, old_pointer = self.layout_work()
        repo = self.config()
        out = [
            (
                f"move {len(moves)} record file(s) under {records.SYSTEM_DIR}/ in "
                f"{repo} (one commit)"
            ),
            *move_lines(moves, SEMESTER_MOVES),
        ]
        if OLD_PEOPLE_FILE in deletes:
            out.append(
                f"{OLD_PEOPLE_FILE} -> {INSTRUCTORS_FILE} (one list, a role each)"
            )
        if records.path("pointer") in files:
            out.append(f"write the course pointer to {records.path('pointer')}")
        samples = [p for p in deletes if p.endswith(SAMPLE_SUFFIX)]
        if samples:
            out.append(f"delete {len(samples)} *{SAMPLE_SUFFIX} file(s)")
            out += [f"  {p}" for p in samples]
        if old_pointer:
            out.append(f"delete {OLD_POINTER_REPO}/{COURSE_CONFIG} (the old pointer)")
        return out

    def layout(self) -> bool:
        repo = self.config()
        moves, files, deletes, old_pointer = self.layout_work()
        if INSTRUCTORS_FILE in files and not files[INSTRUCTORS_FILE]:
            log_err(
                f"{OLD_PEOPLE_FILE} in {self.org} cannot be converted faithfully - write "
                f"{INSTRUCTORS_FILE} by hand (docs/05), delete {OLD_PEOPLE_FILE}, then "
                f"run again. Nothing was written."
            )
            return False
        self.before = _files(self.org, repo)
        self.people = get_file_content(self.org, repo, OLD_PEOPLE_FILE) or ""
        if not move_files(
            self.org, repo, moves, LAYOUT_COMMIT, files=files, delete=deletes
        ):
            return False
        if old_pointer:
            return move_files(
                self.org, OLD_POINTER_REPO, {}, LAYOUT_COMMIT, delete=[COURSE_CONFIG]
            )
        return True

    def layout_verified(self) -> bool:
        repo = self.config()
        live = _files(self.org, repo)
        before = getattr(self, "before", live)
        # Every marker at its new path, byte for byte, and nowhere else.
        for old, new in fold(set(before), SEMESTER_MOVES).items():
            if old in live or live.get(new) != before[old]:
                log_err(f"{repo}: a record did not arrive intact at {new}")
                return False
        if self.people and OLD_PEOPLE_FILE not in live:
            new = get_file_content(self.org, repo, INSTRUCTORS_FILE) or ""
            if yaml.safe_load(self.people) is not None and not same_people(
                self.people, new
            ):
                log_err(
                    f"{INSTRUCTORS_FILE} does not name the people of {OLD_PEOPLE_FILE}"
                )
                return False
        return self.layout_done()

    # keys -------------------------------------------------------------------
    def keys_text(self) -> tuple[str | None, str | None]:
        text = get_file_content(self.org, self.config(), schedule.SCHEDULE_PATH)
        return text, (schedule_keys(text) if text is not None else None)

    def keys_done(self) -> bool:
        """Nothing left for the rewrite, and the new engine reads the file clean. A key
        the line rewrite cannot reach (inside a flow mapping, say) keeps this undone: the
        step then stops, naming the entry to fix by hand."""
        if not self.renamed():
            return False
        text, new = self.keys_text()
        return text == new and _schedule_clean(text, quiet=True)

    def keys(self) -> bool:
        text, new = self.keys_text()
        if text == new:
            return True
        return move_files(
            self.org,
            self.config(),
            {},
            KEYS_COMMIT,
            files={schedule.SCHEDULE_PATH: new.encode()},
        )

    def keys_verified(self) -> bool:
        text, new = self.keys_text()
        return text == new and _schedule_clean(text)

    # topic -------------------------------------------------------------------
    def topics(self) -> set[str]:
        return _topics(_listing(self.org))

    def topic_done(self) -> bool:
        topics = self.topics()
        return SEMESTER_TOPIC in topics and OLD_SEMESTER_TOPIC not in topics

    def retopic(self) -> bool:
        topics = (self.topics() - {OLD_SEMESTER_TOPIC}) | {SEMESTER_TOPIC}
        return set_repo_topics(self.org, ".github", sorted(topics))

    # re-render ---------------------------------------------------------------
    def drift(self) -> list[str]:
        """Every SYSTEM-OWNED file of the semester that is not what this checkout
        renders: the config repo's workflows and README, the pointer, the lock, the
        join repo's forms and workflows and the org READMEs."""
        ref = central_ref_for(self.course)
        wanted = {
            CONFIG_REPO: {
                **config_system_files(ref),
                records.path("pointer"): self.pointer(),
                TEAM_LOCK_PATH: team_lock_content(self.course, self.org),
            },
            JOIN_REPO: join_files(self.org),
            ".github": profile_files(self.org, central_ref=ref),
        }
        return _drift(self.org, wanted)

    def ready_to_render(self) -> bool:
        """Every step before the re-render done: only then does this engine's render of
        the semester (its schedule, its lock) read files that are there."""
        return (
            self.renamed()
            and self.layout_done()
            and self.keys_done()
            and self.topic_done()
        )

    def rerendered(self, *, quiet: bool = False) -> bool:
        """The re-render's verify: every step before it done, and no drift."""
        return self.ready_to_render() and _no_drift(self.drift(), quiet=quiet)

    def rerender_done(self) -> bool:
        return self.pause.record() is None and self.rerendered(quiet=True)

    def rerender(self) -> bool:
        ref = central_ref_for(self.course)
        failures = refresh_join_workflows(self.org)
        failures += refresh_config_system_files(self.org, ref)
        failures += refresh_semester_pointer(self.org, self.course)
        failures += 0 if sync_team_lock(self.course, self.org).ok else 1
        failures += update_profile_readme(self.org, central_ref=ref)
        return failures == 0

    def steps(self) -> list[Step]:
        repo = f"{self.org}/{CONFIG_REPO}"
        return [
            self.pause.steps()[0],
            Step(
                "rename repos",
                done=self.renamed,
                plan=lambda: [
                    f"rename {o} -> {n}" for o, n in self.renames_left().items()
                ],
                do=self.rename,
                verify=self.rename_verified,
                rollback="rename each repo back in its Settings (the old name is free)",
            ),
            Step(
                "layout",
                done=self.layout_done,
                plan=self.layout_plan,
                do=self.layout,
                verify=self.layout_verified,
                rollback=f"git revert the '{LAYOUT_COMMIT}' commit(s) in {repo} and .github",
            ),
            Step(
                "keys",
                done=self.keys_done,
                plan=lambda: [
                    (
                        f"{CONFIG_REPO}/{schedule.SCHEDULE_PATH}: cohort_dest_* -> "
                        f"semester_dest_*, type -> kind on releases and events"
                    )
                ],
                do=self.keys,
                verify=self.keys_verified,
                rollback=f"git revert the '{KEYS_COMMIT}' commit in {repo}",
            ),
            Step(
                "topic",
                done=self.topic_done,
                plan=lambda: [
                    f".github topic {OLD_SEMESTER_TOPIC} -> {SEMESTER_TOPIC}"
                ],
                do=self.retopic,
                verify=self.topic_done,
                rollback=f"set the .github topic back to {OLD_SEMESTER_TOPIC}",
            ),
            Step(
                "re-render",
                done=self.rerender_done,
                plan=lambda: (
                    [
                        "re-write every SYSTEM-OWNED file that differs:",
                        *(f"  {path}" for path in self.drift()),
                        *([RERUN_NOTE] if self.pause.record() is not None else []),
                    ]
                    if self.ready_to_render()
                    else [
                        (
                            "re-write every SYSTEM-OWNED file that differs (listed once "
                            "the steps above have run)"
                        )
                    ]
                ),
                do=self.rerender,
                verify=self.rerendered,
                rollback="the rollbacks of the steps above, in reverse",
            ),
            # status.json before the unpause: re-enabled workflows never race it.
            _status_step(self.course, self.org),
            self.pause.steps()[1],
        ]


# ------------------------------------------------------------------ the course org


class Course:
    def __init__(self, org: str) -> None:
        self.org = org
        self.pause = Pause(org, self.targets)

    def targets(self) -> list[tuple[str, str]]:
        """`.github` and every content repo and template that carries a workflow."""
        return _with_workflows(self.org, sorted(_listing(self.org)))

    # registry ---------------------------------------------------------------
    def registry(self) -> tuple[str | None, str | None]:
        return (
            get_file_content(self.org, ".github", OLD_SEMESTERS_PATH),
            get_file_content(self.org, ".github", SEMESTERS_PATH),
        )

    def registry_done(self) -> bool:
        old, new = self.registry()
        return old is None and "cohorts" not in _yaml(new)

    def migrate_registry(self) -> bool:
        old, new = self.registry()
        source = new if new is not None else old or ""
        files = {SEMESTERS_PATH: registry_keys(source).encode()}
        return move_files(
            self.org,
            ".github",
            {},
            LAYOUT_COMMIT,
            files=files,
            delete=[OLD_SEMESTERS_PATH],
        )

    # .github -------------------------------------------------------------------
    def dotgithub_moves(self) -> dict[str, str]:
        return fold(set(_files(self.org, ".github")), COURSE_MOVES)

    # dsl-course.yml ---------------------------------------------------------------
    def meta_text(self) -> tuple[str | None, str | None]:
        text = get_file_content(self.org, ".github", COURSE_CONFIG)
        return text, (course_config_keys(text) if text is not None else None)

    def lost_values(self) -> list[str]:
        """What stripping this course's old semester-defaults block would lose, each
        value named for a person to carry by hand before the migration strips it."""
        text, _ = self.meta_text()
        lost = lost_semester_values(_yaml(text), policy.defaults())
        for value in lost:
            log_err(
                f"{self.org}/.github/{COURSE_CONFIG}: `{value}` differs from the policy - "
                f"carry it by hand into each live semester's {CONFIG_REPO}/"
                f"{schedule.SCHEDULE_PATH} (`timezone:` / `archive: grace_days:`), then "
                f"delete it from {COURSE_CONFIG} and run again. Nothing was written."
            )
        return lost

    def meta_done(self) -> bool:
        text, new = self.meta_text()
        return text == new

    def meta_verified(self) -> bool:
        """Re-read with this engine's own rules: no retired key, no old spelling in the
        course layer."""
        text, _ = self.meta_text()
        meta = _yaml(text)
        defaults = meta.get(ASSIGNMENT_DEFAULTS_KEY) or {}
        bad = [f.field for f in retired_course_faults(meta)]
        bad += [
            f"{ASSIGNMENT_DEFAULTS_KEY}.{key}"
            for key in RENAMED_SETTINGS
            if key in (defaults if isinstance(defaults, dict) else {})
        ]
        for key in bad:
            log_err(f"{COURSE_CONFIG}: `{key}` is still NOT_MIGRATED")
        return self.meta_done() and not bad

    def rewrite_meta(self) -> bool:
        text, new = self.meta_text()
        if text == new:
            return True
        return move_files(
            self.org, ".github", {}, KEYS_COMMIT, files={COURSE_CONFIG: new.encode()}
        )

    # templates ---------------------------------------------------------------
    def templates(self) -> list[str]:
        return sorted(
            name
            for name, row in _listing(self.org).items()
            if row.get("isTemplate")
            and not row.get("archived")
            and name.startswith("assignment-")
        )

    def grading_text(self, repo: str) -> str | None:
        return get_file_content(self.org, repo, GRADING_FILE, ref=SOLUTION_BRANCH)

    def templates_left(self) -> dict[str, str]:
        """`{template: its rewritten grading_config.yml}` for each that needs it."""
        out = {}
        for repo in self.templates():
            text = self.grading_text(repo)
            if text is not None and grading_config_keys(text) != text:
                out[repo] = grading_config_keys(text)
        return out

    def rewrite_templates(self) -> bool:
        return all(
            move_files(
                self.org,
                repo,
                {},
                KEYS_COMMIT,
                files={GRADING_FILE: text.encode()},
                branch=SOLUTION_BRANCH,
            )
            for repo, text in self.templates_left().items()
        )

    def templates_verified(self) -> bool:
        bad = [r for r in self.templates() if not _grading_clean(self.grading_text(r))]
        for repo in bad:
            log_err(f"{repo}@{SOLUTION_BRANCH}/{GRADING_FILE} is still NOT_MIGRATED")
        return not self.templates_left() and not bad

    # materials ---------------------------------------------------------------
    def materials_moves(self) -> dict[str, dict[str, str]]:
        return {
            repo: moves
            for repo in sorted(_listing(self.org))
            if (moves := fold(set(_files(self.org, repo)), MATERIALS_MOVES))
            and repo.startswith(MATERIALS_REPO_PREFIX)
        }

    def move_materials(self) -> bool:
        """Every repo's moves checked before the first is written: a clash in one repo
        must not land after another's commit."""
        work = self.materials_moves()
        if any(
            refuse_clashes(self.org, r, _files(self.org, r), m) for r, m in work.items()
        ):
            return False
        return all(
            move_files(self.org, repo, moves, LAYOUT_COMMIT)
            for repo, moves in self.materials_moves().items()
        )

    # re-render ---------------------------------------------------------------
    def drift(self) -> list[str]:
        """Every file the course re-render (`seed.refresh`) writes that is not what this
        checkout renders: the `.github` workflows, each content repo's release workflows
        (and a materials repo's system files), each live template's hand-out workflow -
        and a retired workflow still lying in a content repo or template."""
        ref = central_ref_for(self.org)
        semesters = discover_semesters(self.org)
        templates = discover_assignment_repos(self.org)
        assignments = [r["name"] for r in templates]

        def hosted(repo: str, workflows: tuple[str, ...]) -> dict[str, bytes]:
            return content_workflow_files(
                semesters, assignments, repo, ref, workflows=workflows
            )

        wanted = {".github": seed.github_workflow_files(self.org, ref)}
        for repo in discover_content_repos(self.org):
            wanted[repo] = hosted(repo, RELEASE_WORKFLOWS)
            if repo.startswith(MATERIALS_REPO_PREFIX):
                wanted[repo] |= materials_system_files(self.org, repo)
        for row in templates:
            if not row.get("archived"):
                wanted[row["name"]] = hosted(row["name"], TEMPLATE_WORKFLOWS)
        retired = [
            f"{repo}/{p} (retired)"
            for repo in wanted
            if repo != ".github"
            for p in RETIRED_WORKFLOWS
            if p in _files(self.org, repo)
        ]
        return _drift(self.org, wanted) + retired

    def rerendered(self, *, quiet: bool = False) -> bool:
        """The re-render's verify: the registry migrated, and no drift."""
        return self.registry_done() and _no_drift(self.drift(), quiet=quiet)

    def rerender_done(self) -> bool:
        return self.pause.record() is None and self.rerendered(quiet=True)

    def work_done(self) -> bool:
        """Every step of the course's migration done - what a semester waits for."""
        return all(
            s.done() for s in self.steps() if not s.bracket and s.name != "status"
        )

    def steps(self) -> list[Step]:
        dotgithub = f"{self.org}/.github"
        return [
            self.pause.steps()[0],
            Step(
                "registry",
                done=self.registry_done,
                plan=lambda: [
                    (
                        f".github/{OLD_SEMESTERS_PATH} -> {SEMESTERS_PATH}, "
                        f"cohorts: -> semesters:"
                    )
                ],
                do=self.migrate_registry,
                verify=self.registry_done,
                rollback=f"git revert the '{LAYOUT_COMMIT}' commit in {dotgithub}",
            ),
            Step(
                f"{records.SYSTEM_DIR}/ in .github",
                done=lambda: not self.dotgithub_moves(),
                plan=lambda: [
                    (
                        f"move {len(self.dotgithub_moves())} file(s) under "
                        f"{records.SYSTEM_DIR}/"
                    ),
                    *move_lines(self.dotgithub_moves(), COURSE_MOVES),
                ],
                do=lambda: move_files(
                    self.org, ".github", self.dotgithub_moves(), LAYOUT_COMMIT
                ),
                verify=lambda: not self.dotgithub_moves(),
                rollback=f"git revert the '{LAYOUT_COMMIT}' commit in {dotgithub}",
            ),
            Step(
                f"{COURSE_CONFIG} keys",
                done=self.meta_done,
                plan=lambda: [
                    (
                        f"remove {', '.join(RETIRED_COURSE_KEYS)}; "
                        "assignment_defaults format: -> formats:"
                    )
                ],
                do=self.rewrite_meta,
                verify=self.meta_verified,
                rollback=f"git revert the '{KEYS_COMMIT}' commit in {dotgithub}",
            ),
            Step(
                "template keys",
                done=lambda: not self.templates_left(),
                plan=lambda: [
                    f"{r}@{SOLUTION_BRANCH}/{GRADING_FILE}: format: -> formats:"
                    for r in self.templates_left()
                ],
                do=self.rewrite_templates,
                verify=self.templates_verified,
                rollback=(
                    f"git revert the '{KEYS_COMMIT}' commit on each template's "
                    f"{SOLUTION_BRANCH} branch"
                ),
            ),
            Step(
                "materials files",
                done=lambda: not self.materials_moves(),
                plan=lambda: [
                    line
                    for repo, m in self.materials_moves().items()
                    for line in (
                        (
                            f"{repo}: move {len(m)} system file(s) under "
                            f"{records.SYSTEM_DIR}/"
                        ),
                        *move_lines(m, MATERIALS_MOVES),
                    )
                ],
                do=self.move_materials,
                verify=lambda: not self.materials_moves(),
                rollback=f"git revert the '{LAYOUT_COMMIT}' commit in each materials repo",
            ),
            Step(
                "re-render",
                done=self.rerender_done,
                plan=lambda: (
                    [
                        "Refresh actions from this checkout; these files differ now:",
                        *(f"  {path}" for path in self.drift()),
                        *([RERUN_NOTE] if self.pause.record() is not None else []),
                    ]
                    if self.registry_done()
                    else [
                        (
                            "Refresh actions from this checkout (the files are listed once "
                            "the registry step has run)"
                        )
                    ]
                ),
                do=lambda: seed.refresh(self.org, course_only=True) == 0,
                verify=self.rerendered,
                rollback="the rollbacks of the steps above, in reverse",
            ),
            _status_step(self.org, None),
            self.pause.steps()[1],
        ]


# ------------------------------------------------------------------ preflight


def _pointer_course(org: str, listing: dict[str, dict]) -> str:
    """The course org a semester points at, from whichever pointer it has now."""
    if CONFIG_REPO in listing:
        text = get_file_content(org, CONFIG_REPO, records.path("pointer"))
        if text is not None:
            return str(_yaml(text).get("course") or "")
    old = get_file_content(org, OLD_POINTER_REPO, COURSE_CONFIG)
    return str(_yaml(old).get("course") or "")


def preflight(org: str) -> Course | Semester | None:
    """What `org` is, and whether it may be migrated. None (with the reason logged) when
    it may not. Reads only."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        log_err("the migration runs from a maintainer's laptop, never from a workflow")
        return None
    listing = _listing(org)
    topics = _topics(listing)
    if ".github" not in listing:
        log_err(f"{org} has no .github repo - not a bootstrapped org")
        return None
    if COURSE_HUB_TOPIC in topics:
        if listing[".github"].get("archived"):
            log_err(f"{org}/.github is archived - an archived course is never touched")
            return None
        target: Course | Semester = Course(org)
        if target.lost_values():
            return None
    elif topics & {OLD_SEMESTER_TOPIC, SEMESTER_TOPIC}:
        config = listing.get(CONFIG_REPO) or listing.get(OLD_CONFIG_REPO)
        if (
            config is None
            or config.get("archived")
            or all(r.get("archived") for r in listing.values())
        ):
            log_err(
                f"{org} is archived (or has no config repo) - archived semesters are "
                f"never touched"
            )
            return None
        course = _pointer_course(org, listing)
        if not course:
            log_err(f"{org}'s course pointer names no course org")
            return None
        parent = Course(course)
        target = Semester(org, course)
        # The course is complete when every step of its own migration is done, its own
        # pause is released (no record left), and its Actions are on - unless they are
        # off because THIS semester's migration paused them and stopped (its record is
        # there): then this run resumes it. Another semester's pause in flight shows as
        # the course's Actions off with no record here, and is refused.
        resuming = target.pause.record() is not None
        course_on = parent.pause.record() is None and all(
            _actions_enabled(o, r) for o, r in parent.targets()
        )
        if not parent.work_done() or parent.pause.record() is not None:
            log_err(
                f"{course} is not fully migrated - finish that first: "
                f"`python -m dsl_course.migrate {course} --no-preview`, then this semester"
            )
            return None
        if not (course_on or resuming):
            log_err(
                f"another semester's migration under {course} is in flight (the course's "
                f"Actions are off, with no pause record of {org}'s) - let it finish, then "
                f"this semester"
            )
            return None
    else:
        log_err(
            f"{org}'s .github carries neither {COURSE_HUB_TOPIC} nor a semester topic"
        )
        return None
    alive = _alive(target.targets())
    if alive:
        log_err(
            f"a workflow run is queued or running in {', '.join(alive)} - wait for it "
            f"to finish, then re-run the migration"
        )
        return None
    return target


ROOT = Path(__file__).resolve().parents[1]
_SHA = re.compile(r"[0-9a-f]{40}")


def checkout_drift(ref: str) -> list[str] | None:
    """The engine and template files where THIS checkout differs from `ref` (as of the
    last fetch), uncommitted edits included; None when git cannot say."""
    base = ref if _SHA.fullmatch(ref) else f"origin/{ref}"
    code, out = git(
        "diff", "--name-only", base, "--", "dsl_course", "templates", cwd=str(ROOT)
    )
    if code != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def _warn_drift(course: str, ref: str) -> None:
    """Say, by file, where the checkout that renders differs from what the org runs."""
    drift = checkout_drift(ref)
    if drift is None:
        log_err(
            f"could not compare this checkout with {ref} (the ref {course} runs) - "
            f"check out {ref} so the migration renders what the org will run"
        )
    elif drift:
        shown = ", ".join(drift[:20]) + (" ..." if len(drift) > 20 else "")
        log_err(
            f"this checkout differs from {ref}, the ref {course} runs, in "
            f"{len(drift)} file(s): {shown}. The re-render writes what THIS checkout "
            f"says and the org's next Refresh writes what {ref} says - check out {ref}, "
            f"or pin the course to this code first"
        )


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument("org", help="The course org or semester org to migrate")
    add_preview_flag(parser, "Print the plan and write nothing (default).")
    args = parser.parse_args()
    _forget()
    try:
        target = preflight(args.org)
        if target is None:
            return 1
        course = target.course if isinstance(target, Semester) else args.org
        ref = central_ref_for(course)
    except RuntimeError as exc:
        log_err(f"preflight could not read {args.org}: {exc}")
        return 1
    log(f"  central ref of the course: {ref}")
    _warn_drift(course, ref)
    return run(args.org, target.steps(), args.preview, target.pause)


if __name__ == "__main__":
    sys.exit(main())
