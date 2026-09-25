"""migrate - convert one live org to the names and layout of decisions 0010 and 0012.

The engine knows only the new names: an old one is a hard NOT_MIGRATED fault. This is the
one module that knows the OLD ones, and the one thing that moves an org from them. It runs
from a maintainer's laptop with the maintainer's token, never from a workflow, so a run is
one person's deliberate act; the maintainer runs it from the toolkit checkout that the
org's `central_ref` will run once it is migrated.

    python3 -m dsl_course.migrate <org>                 # preview: the plan, nothing written
    python3 -m dsl_course.migrate <org> --no-preview    # do it, step by step
    python3 -m dsl_course.migrate --hold <course>       # pause every org of the course
    python3 -m dsl_course.migrate --release <course>    # ... and restore them
    python3 -m dsl_course.migrate --status <course>     # who is paused

A course org is migrated before any of its semesters. The org's tier comes from its
`.github` topic. Every step says what it will do, does it, verifies it, and stops on the
first failed verification naming that step's rollback. Every step is idempotent: a step
whose work is already done says "already migrated", so a second run changes nothing and a
run that stopped part-way resumes where it stopped. An archived semester is never touched.

The pause is GitHub's own switch: Actions are DISABLED on every repo of the org that runs
workflows, for the window, and enabled again at the end. (An org variable cannot do this:
on GitHub Free org variables do not reach private repos, and the workflows already live in
an org carry no gate until they are re-rendered.) It waits for quiet before it switches
anything. GitHub drops, rather than queues, what fires into a disabled repo, so the unpause
dispatches one Scheduled release and one Sync membership in its place, and waits (bounded)
for those runs, so the next org's migration finds the course quiet. For a real course
the tier moves inside a HOLD of every org of the course (`--hold`, `--release`); a
migration under a hold neither pauses nor unpauses.

Semester org, in order: preflight, pause, rename repos, layout, keys, topic, re-render,
status, unpause. Course org: preflight, pause, registry, .system/, dsl-course.yml keys,
template keys, materials files, re-render, status, unpause.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from time import time as clock

import yaml

from . import cadence, policy, records, schedule, seed, settings, status
from .bootstrap_course import semester_scaffold
from .central import CENTRAL
from .course import (
    ASSIGNMENTS_FILE,
    CONFIG_REPO,
    COURSE_CONFIG,
    COURSE_HUB_TOPIC,
    INSTRUCTORS_FILE,
    JOIN_REPO,
    MAINTAINING_FILE,
    MATERIALS_REPO_PREFIX,
    MIGRATE_DRIVER,
    OLD_CONFIG_REPO,
    OLD_JOIN_REPO,
    OLD_PEOPLE_FILE,
    OLD_SEMESTER_TOPIC,
    PUBLISH_FILE,
    RETIRED_COURSE_KEYS,
    SEMESTER_TOPIC,
    SOLUTION_BRANCH,
    SUBMIT_VIA,
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
from .materials import MATERIALS_TOPIC, is_materials_repo
from .profile_readme import profile_files, update_profile_readme
from .repos import (
    current_description,
    default_branch,
    repo_missing,
    set_repo_topics,
)
from .scaffold import PUBLISH_HEADER, materials_system_files
from .setting_readers import RENAMED_SETTINGS, read_settings
from .settings import ASSIGNMENT_DEFAULTS_KEY, RUN_KEYS
from .sync_faculty import retired_course_faults
from .welcome import (
    RETIRED_JOIN_FORMS,
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
# The course's `.github`: the run settings the template-keys step took out of each
# template, `{template: {key: value}}` - what its semesters' keys steps write into their
# `assignments.yml`, since the course is migrated first. Public, like the repo: run
# settings name no person.
RUN_KEYS_RECORD = records.path("migration_run_keys")
REPO_RENAMES = {OLD_CONFIG_REPO: CONFIG_REPO, OLD_JOIN_REPO: JOIN_REPO}
# The console op ids that said `cohort` (decision 0012). A semester's outcome record is
# named by its op id and carries it in `op`, which status.json's `operations` repeats.
OP_RENAMES = {
    f"cohort.{name}": f"semester.{name}"
    for name in ("check", "preview_automation", "archive", "bootstrap")
}
OUTCOMES_DIR = records.path("outcomes")
SAMPLE_SUFFIX = ".sample"
WORKFLOWS_DIR = ".github/workflows/"
LAYOUT_COMMIT = "migrate: layout"
TEXT_COMMIT = "migrate: seeded text"
PROFILE_README = "profile/README.md"
JOIN_README = "README.md"
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


def renamed_outcome(path: str) -> str | None:
    """The path an outcome record at `path` takes under its op's new id, or None when
    `path` is no record of a renamed op."""
    for old, new in OP_RENAMES.items():
        if path == f"{OUTCOMES_DIR}/{old}.json":
            return f"{OUTCOMES_DIR}/{new}.json"
    return None


def outcome_text(text: str, op: str) -> bytes:
    """An outcome record's text with its `op` set to `op`, in `write_private`'s layout. A
    record that does not parse is carried over as it is."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text.encode()
    if not isinstance(data, dict):
        return text.encode()
    data["op"] = op
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


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


# Keys schedule.yml no longer takes (decision 0009): `assignments.<key>.` title,
# grading_datetime and semester_dest_repo (its old spelling included), a release's
# `assignment:` and the top-level `enrolment:` block.
RETIRED_ASSIGNMENT_KEYS = ("title", "grading_datetime", "semester_dest_repo")
_RETIRED_IN = {
    "assignments": RETIRED_ASSIGNMENT_KEYS,
    "releases": ("assignment",),
}
RETIRED_TOP = ("enrolment",)


def schedule_timing(text: str) -> str:
    """`schedule.yml` without the keys that left it (`RETIRED_ASSIGNMENT_KEYS` on an
    assignment entry, `assignment:` on a release, the `enrolment:` block), each with the
    lines of its value. Run after `schedule_keys`, so `cohort_dest_repo` is spelt
    `semester_dest_repo` by then."""
    out, section = [], ""
    scalar: int | None = None  # the column of the key whose block scalar this is in
    cut: int | None = None  # the column of the key being removed, with its value
    for line in text.split("\n"):
        if cut is not None:
            if line.strip() and _indent(line) > cut:
                continue
            cut = None
        if scalar is not None and (not line.strip() or _indent(line) > scalar):
            out.append(line)
            continue
        scalar = None
        if top := _TOP_KEY.match(line):
            section = top.group(1)
            if section in RETIRED_TOP:
                cut = 0
                continue
        keys = _RETIRED_IN.get(section, ())
        found = re.match(r"^(\s+)([A-Za-z_][\w-]*):", line)
        if found and found.group(2) in keys and _indent(line) >= 4:
            cut = len(found.group(1))
            continue
        if block := _BLOCK_SCALAR.match(line):
            scalar = len(block.group(1))
        out.append(line)
    return "\n".join(out)


def strip_run_keys(text: str) -> str:
    """A template's `grading_config.yml` without its run settings (`settings.RUN_KEYS`),
    live or commented out, each with the indented comment lines that continue it: they
    are set per semester in `assignments.yml` now."""
    keys = "|".join(RUN_KEYS)
    out, cut = [], False
    for line in text.split("\n"):
        if cut and re.match(r"^\s+#", line):
            continue
        cut = bool(re.match(rf"^#?\s*({keys}):", line))
        if cut and out and not out[-1].strip():
            out.append("")  # a blank line kept only once where a block went
        if not cut:
            out.append(line)
    return re.sub(r"\n\n\n+", "\n\n", "\n".join(out))


def template_run_keys(text: str | None) -> dict:
    """The run settings a template's `grading_config.yml` states, as written."""
    data = _yaml(text)
    return {key: data[key] for key in RUN_KEYS if key in data}


def _read_run_keys(raw: dict) -> dict:
    """`raw` through the settings readers, refused values dropped (the migration keeps
    what the engine could use, and nothing it would refuse)."""
    values = read_settings(raw, RUN_KEYS, "migrate", [])
    return {k: v for k, v in values.items() if v is not None or k in settings.LATE_PAIR}


def instance_additions(
    meta: dict,
    templates: dict[str, dict],
    lower: list,
    existing: settings.Instance,
) -> tuple[dict[str, dict], list[str]]:
    """What the semester's `assignments.yml` must say so this engine runs each assignment
    of `meta` (the OLD `schedule.yml`, parsed) as the old one did: `{key: {setting:
    value}}`, and a note for each late cutoff that moves by more than an hour.

    - the template's run settings (`templates`, `{template repo: raw run keys}`) that the
      layers below (`lower`: the semester's defaults, the course, the institution) would
      not give;
    - `late_window_days` (with the penalty beside it) where `grading_datetime` was not the
      due date plus the window: the cutoff is computed now, rounded to whole days;
    - `semester_dest_repo`, moved out of the schedule.
    A setting the file already states is left as written."""
    tz = schedule._tz(meta.get("timezone"))
    out: dict[str, dict] = {}
    notes: list[str] = []
    entries = meta.get("assignments")
    for slug, entry in entries.items() if isinstance(entries, dict) else ():
        if not isinstance(entry, dict):
            continue
        slug = str(slug)
        own = _read_run_keys(templates.get(str(entry.get("course_source_repo")), {}))
        stack = [("template", own), *lower]
        want: dict = {}
        for key in settings.RUN_KEYS:
            if key in settings.LATE_PAIR:
                continue
            value, _ = settings.resolve(key, stack)
            if value != settings.resolve(key, lower)[0]:
                want[key] = value
        pair = {k: settings.resolve(k, stack)[0] for k in settings.LATE_PAIR}
        due = schedule._coerce_datetime(entry.get("due_datetime"), tz, end_of_day=True)
        pin = schedule._coerce_datetime(
            entry.get("grading_datetime"), tz, end_of_day=True
        )
        if due is not None and pin is not None:
            days = max(0, round((pin - due).total_seconds() / 86400))
            moved = due + timedelta(days=days) - pin
            if abs(moved.total_seconds()) > 3600:
                notes.append(
                    f"assignments.{slug}: the late cutoff moves from {pin:%Y-%m-%d %H:%M} "
                    f"to {due + timedelta(days=days):%Y-%m-%d %H:%M} (whole days after "
                    f"the due date)"
                )
            pair["late_window_days"] = days
        if pair != {k: settings.resolve(k, lower)[0] for k in settings.LATE_PAIR}:
            want |= {k: v for k, v in pair.items() if v is not None}
        dest = str(
            entry.get("semester_dest_repo") or entry.get("cohort_dest_repo") or ""
        ).strip()
        if dest:
            want["semester_dest_repo"] = dest
        stated = existing.blocks.get(slug, {}).keys() | (
            {"semester_dest_repo"} if slug in existing.dests else set()
        )
        if settings.LATE_PAIR[0] in stated or settings.LATE_PAIR[1] in stated:
            stated |= set(settings.LATE_PAIR)
        want = {k: v for k, v in want.items() if k not in stated}
        if want:
            out[slug] = want
    return out, notes


def _scalar(value: object) -> str:
    return (
        yaml.safe_dump(value, default_flow_style=True).removesuffix("\n...\n").strip()
    )


def with_instance_keys(text: str, additions: dict[str, dict]) -> str:
    """`assignments.yml` (`text`) with `additions` written into its `assignments:` block,
    each key under its slug's block (created when absent) - comments and layout kept.
    Assumes the two-space layout the skeleton uses; the caller re-reads the result."""
    lines = text.split("\n")
    at = next((i for i, ln in enumerate(lines) if re.match(r"^assignments:", ln)), None)
    if at is None:
        body = "\n".join(
            [f"{settings.ASSIGNMENTS_BLOCKS}:"]
            + [
                row
                for slug, keys in additions.items()
                for row in (
                    f"  {slug}:",
                    *(f"    {k}: {_scalar(v)}" for k, v in keys.items()),
                )
            ]
        )
        return text.rstrip("\n") + "\n\n" + body + "\n"
    for slug, keys in additions.items():
        rows = [f"    {k}: {_scalar(v)}" for k, v in keys.items()]
        block = next(
            (i for i, ln in enumerate(lines) if i > at and ln.startswith(f"  {slug}:")),
            None,
        )
        if block is None:
            lines[at + 1 : at + 1] = [f"  {slug}:", *rows]
        else:
            lines[block + 1 : block + 1] = rows
    return "\n".join(lines)


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


# ------------------------------------------------------------------ seeded text
# Repo names and paths that moved (decisions 0010 and 0012), inside text the toolkit
# seeded: rewritten directly - a link to an old name is not a wording choice (and
# `semester-config/people.yml`, which never existed: an earlier pass renamed the repo
# of a path it missed at a sentence's end). Only the
# org's OWN repos: a link into another org (an archived semester, never migrated) still
# resolves where it is. A bare name counts only as a whole name, never inside another
# (`old-classroom-config`), and `welcome` only as a link to this org's repo (it is also a
# word). Longest first, so a path is not caught by its repo's name alone.
_WHOLE = r"(?<![\w./-])"
_END = r"(?![\w-])"
# A file name's end: a full stop that ends the sentence is not `people.yml.sample`.
_FILE_END = r"(?!\w|\.\w)"


def text_renames(org: str, *, registry: bool = False) -> list[tuple[re.Pattern, str]]:
    """The rewrites for text in `org`; `registry` adds the course registry's old file
    name, which only the course's own `dsl-course.yml` names."""
    site = rf"github\.com/{re.escape(org)}/"
    out = [
        (
            re.compile(
                rf"({site}){OLD_CONFIG_REPO}(/blob/[^/\s]+/){re.escape(OLD_PEOPLE_FILE)}"
                rf"{_FILE_END}"
            ),
            rf"\g<1>{CONFIG_REPO}\g<2>{INSTRUCTORS_FILE}",
        ),
        (re.compile(rf"({site}){OLD_CONFIG_REPO}{_END}"), rf"\g<1>{CONFIG_REPO}"),
        (
            re.compile(
                rf"\[`{OLD_JOIN_REPO}`\]\((https?://{site}){OLD_JOIN_REPO}{_END}"
            ),
            rf"[`{JOIN_REPO}`](\g<1>{JOIN_REPO}",
        ),
        (re.compile(rf"({site}){OLD_JOIN_REPO}{_END}"), rf"\g<1>{JOIN_REPO}"),
        (
            re.compile(
                rf"{_WHOLE}(?:{OLD_CONFIG_REPO}|{CONFIG_REPO})/"
                rf"{re.escape(OLD_PEOPLE_FILE)}{_FILE_END}"
            ),
            f"{CONFIG_REPO}/{INSTRUCTORS_FILE}",
        ),
        (re.compile(rf"{_WHOLE}{OLD_CONFIG_REPO}{_END}"), CONFIG_REPO),
    ]
    if registry:
        out.append(
            (
                re.compile(rf"(?<![\w-]){re.escape(OLD_SEMESTERS_PATH)}"),
                SEMESTERS_PATH,
            )
        )
    return out


# Words a person may have written: listed for review, never rewritten.
_OLD_WORD = re.compile(r"(?i)\bcohorts?\b")


def renamed(text: str, org: str, *, registry: bool = False) -> str:
    """`text` in `org` with every old repo name and path of `text_renames` rewritten."""
    for old, new in text_renames(org, registry=registry):
        text = old.sub(new, text)
    return text


def seeded_wording(ref: str) -> dict[str, str]:
    """`{line as the old templates seeded it: the new template's line}`, for the seeded
    lines that said `cohort` or named a deleted sample. A line still exactly as seeded is
    the toolkit's wording, not the instructor's, so it takes the new one. The new side is
    held to the current templates by `tests/test_migrate.py`."""
    schedule_example = _worked_example(ref, schedule.SCHEDULE_PATH)
    submit_via = (
        "assignment_repo (they push to their repo) | external (handed in elsewhere: "
        "Moodle, Kaggle, in class - no repo is created) | shared_dropbox_repo (one "
        "private repo for the whole {}, each student pushes into their own folder, "
        "peers can read it)"
    )
    hand_marked = "hand-marked: a drop box is one repo for the whole {}"
    # A template's grading_config.yml lines, as New assignment wrote them (`scaffold.
    # _setting`: `key: value` padded to column 29, then the comment).
    template_lines = {
        f"{f'submit_via: {shape}':<29} # {submit_via.format('cohort')}": (
            f"{f'submit_via: {shape}':<29} # {submit_via.format('semester')}"
        )
        for shape in SUBMIT_VIA
    } | {
        f"{f'{key}: false':<29} # {hand_marked.format('cohort')}": (
            f"{f'{key}: false':<29} # {hand_marked.format('semester')}"
        )
        for key in ("autograde", "completion_check", "grader_pdf")
    }
    return template_lines | {
        # semester-config/schedule.yml
        "# This cohort's schedule + auto-release plan. Instructors edit it directly.": (
            "# This semester's schedule + auto-release plan. Instructors edit it directly."
        ),
        "# - see reference: https://github.com/hertie-dsl-demo-f2026/classroom-config/"
        "blob/main/schedule.yml": f"# - worked example: {schedule_example}",
        "#   releases:     entries that DEPLOY materials (course org -> this cohort org)": (
            "#   releases:     entries that DEPLOY materials (course org -> this semester "
            "org)"
        ),
        "# What follows is a SKELETON: uncomment and fill what you want. For a full "
        "worked term -": (
            "# What follows is a SKELETON: uncomment and fill what you want. For a full "
            "worked semester -"
        ),
        "# a real release plan, a group project, exams - see `schedule.yml.sample`.": (
            "# a real release plan, a group project, exams - see the worked example above."
        ),
        "# semester_start:                   # OPTIONAL - default: inferred from the "
        "cohort tag (f2026 -> 1 Sep 2026)": (
            "# semester_start:                   # OPTIONAL - default: inferred from the "
            "semester tag (f2026 -> 1 Sep 2026)"
        ),
        "#         cohort_dest_repo:         # OPTIONAL - default: materials": (
            "#         semester_dest_repo:         # OPTIONAL - default: materials"
        ),
        "#         cohort_dest_path:         # OPTIONAL - default: mirrors "
        "course_source_path": (
            "#         semester_dest_path:         # OPTIONAL - default: mirrors "
            "course_source_path"
        ),
        "#         cohort_dest_repo:": "#         semester_dest_repo:",
        "#         cohort_dest_path:": "#         semester_dest_path:",
        "  title: Cohort archived      # optional - the row's Title column": (
            "  title: Semester archived      # optional - the row's Title column"
        ),
        '  show_on_site: true          # a "Cohort archived" row on the site\'s Schedule '
        "tab, also sends a notice email 14 days out": (
            '  show_on_site: true          # a "Semester archived" row on the site\'s '
            "Schedule tab, also sends a notice email 14 days out"
        ),
        "    This cohort is archived on {date}: every repository in it becomes "
        "read-only. You keep read access, so you can still fork or clone anything you "
        "want to keep working on into your own account.": (
            "    This semester is archived on {date}: every repository in it becomes "
            "read-only. You keep read access, so you can still fork or clone anything "
            "you want to keep working on into your own account."
        ),
        # semester-config/people.yml, now instructors.yml
        "# This cohort's own instructors/TAs": (
            "# This semester's own instructors: its instructors and teaching assistants, "
            "one list."
        ),
        "# - the SSOT for who is emailed when this cohort needs attention (see `email` "
        "below).": (
            "# - the SSOT for who is emailed when this semester needs attention (see "
            "`email` below)."
        ),
        "#                                     # notifications about this cohort go "
        "here. Private: not shown on the cohort site unless the entry adds "
        "`show_email: true`": (
            "#                                     # notifications about this semester "
            "go here. Private: not shown on the semester site unless the entry adds "
            "`show_email: true`"
        ),
        '#       photo: "/_images/pp/jane.jpg" # optional. Either (1) a relative path to '
        "an image committed under `_images/pp/` in this cohort's site repo,": (
            '#     photo: "/_images/pp/jane.jpg"   # optional. Either (1) a relative path '
            "to an image committed under `_images/pp/` in this semester's site repo,"
        ),
        # a template's grading_config.yml (its first line)
        "# INSTRUCTOR-OWNED - defines the assignment. Dates live in the cohort's "
        "schedule.yml.": (
            "# INSTRUCTOR-OWNED - defines the assignment. Dates live in the semester's "
            "schedule.yml."
        ),
        # the course's dsl-course.yml
        "  # cohort's own teaching team (instructors & TAs) goes in that cohort's": (
            "  # semester's own teaching team (instructors & TAs) goes in that semester's"
        ),
        "#   # else.) Cohorts inherit this; setting it in a cohort's own file does "
        "nothing.": (
            "#   # else.) Semesters inherit this; setting it in a semester's own file "
            "does nothing."
        ),
        "# course_description: One or two sentences, on ONE line - the blurb on every "
        "cohort site.": (
            "# course_description: One or two sentences, on ONE line - the blurb on "
            "every semester site."
        ),
        "# site_link_extensions: [pdf, html, ipynb]   # OPTIONAL: on the COHORT sites, "
        "link ONLY": (
            "# site_link_extensions: [pdf, html, ipynb]   # OPTIONAL: on the SEMESTER "
            "sites, link ONLY"
        ),
        "#   # WHICH of those files the cohort site hosts publicly, so an HTML deck "
        "opens rendered": (
            "#   # WHICH of those files the semester site hosts publicly, so an HTML deck "
            "opens rendered"
        ),
        "# `course_name`, `course_code` and `course_description` are what reach the "
        "cohort": (
            "# `course_name`, `course_code` and `course_description` are what reach the "
            "semester"
        ),
        "# websites. Editing them here re-syncs every cohort site already bootstrapped "
        "from this": (
            "# websites. Editing them here re-syncs every semester site already "
            "bootstrapped from this"
        ),
        "# This is the persistent COURSE org - it spans many cohorts (years). Cohorts "
        "are": (
            "# This is the persistent COURSE org - it spans many semesters (years). "
            "Semesters are"
        ),
    }


# The course `dsl-course.yml`'s people header as the old template seeded it: a block,
# because the new one is two lines longer. The last line in both spellings a live course
# carries: as seeded, and as the first rehearsal's rename left it.
OLD_PEOPLE_HEADER = """\
# ---------------------------------------------------------------------------
# People. Two DIFFERENT things live under the `people:` key below - don't
# confuse them:
#
#   course_admins        GRANTS ACCESS. The single source of truth for course-wide
#                        admin rights - the `course-admin` team here, mirrored into
#                        every cohort org. A handle added any other way is reverted
#                        on the next sync unless it's declared here; removing a
#                        handle here revokes their access.
#
#   instructors          DISPLAY ONLY - website cards (name/photo/title/link) for
#                        the OPTIONAL public open-courseware course website (the
#                        "Publish course website" action). They grant NO GitHub
#                        access. TAs are NOT declared here - they change every
#                        cohort, so a cohort's whole teaching team (instructors &
#                        TAs, their GitHub access AND their cohort-site cards) is
#                        declared PER COHORT in that cohort's classroom-config/people.yml.
# ---------------------------------------------------------------------------
"""


def seeded_blocks() -> list[tuple[list[str], str]]:
    """`[(the old block's lines, the new template's block)]`, each old spelling once."""
    new = template("course/people-header.yml")
    old = OLD_PEOPLE_HEADER.rstrip("\n")
    return [
        (variant.split("\n"), new)
        for variant in (
            old,
            old.replace(
                f"{OLD_CONFIG_REPO}/{OLD_PEOPLE_FILE}.",
                f"{CONFIG_REPO}/{OLD_PEOPLE_FILE}.",
            ),
        )
    ]


def _replace_block(text: str, old: list[str], new: str) -> str:
    """`text` with its first run of lines equal to `old` (line endings aside) replaced by
    `new`, in the line ending of the run's first line."""
    lines = text.split("\n")
    bare = [line.rstrip("\r") for line in lines]
    for i in range(len(bare) - len(old) + 1):
        if bare[i : i + len(old)] == old:
            eol = "\r" if lines[i].endswith("\r") else ""
            lines[i : i + len(old)] = [
                line + eol for line in new.rstrip("\n").split("\n")
            ]
            break
    return "\n".join(lines)


def seeded_yaml(text: str, ref: str, org: str, *, registry: bool = False) -> str:
    """A seeded YAML file in `org` with every block (`seeded_blocks`) and every line
    (`seeded_wording`) still exactly as the old template seeded it replaced by the new
    template's (the line ending kept), and the old repo names renamed in its comment
    lines (`renamed`). Values and the instructor's own comments are otherwise theirs."""
    wording = seeded_wording(ref)
    for old, new in seeded_blocks():
        text = _replace_block(text, old, new)
    out = []
    for line in text.split("\n"):
        if line.rstrip() in wording:
            line = wording[line.rstrip()] + ("\r" if line.endswith("\r") else "")
        elif line.lstrip().startswith("#"):
            line = renamed(line, org, registry=registry)
        out.append(line)
    return "\n".join(out)


def review_lines(text: str) -> list[int]:
    """The line numbers of `text` that still say `cohort`: a person's words, for them to
    reword - listed, never rewritten, and never quoted (they may name someone)."""
    return [n for n, line in enumerate(text.split("\n"), 1) if _OLD_WORD.search(line)]


def review_plan(texts: dict[str, str]) -> list[str]:
    """The plan's "wording for the instructor to review" block for `texts` (`{"repo/path":
    the text as the step leaves it}`), or nothing when none says `cohort`."""
    found = {
        where: lines for where, text in texts.items() if (lines := review_lines(text))
    }
    if not found:
        return []
    return [
        "wording for the instructor to review (left as it is):",
        *(
            f"  {where}: line(s) {', '.join(map(str, lines))}"
            for where, lines in found.items()
        ),
    ]


# ------------------------------------------------------------------ publish.yml
# A materials repo's `publish.yml` header as New materials repo seeded it before #330: it
# said a pattern matches the SEMESTER copy, the opposite of the rule now (patterns match
# SOURCE paths; a negated folder excludes its subtree). The file is instructor-owned, but
# a header still exactly as seeded is the toolkit's wording, so it takes the current one.
# Two spellings are live: as first seeded (`cohort`), and after the 0012 rename.
OLD_PUBLISH_HEADER = f"""\
# INSTRUCTOR-OWNED - yours. Written once when this repo was scaffolded, and never
# rewritten by the toolkit, so anything you put here stays.
#
# What the cohort site hosts PUBLICLY, so a rendered deck opens in a browser instead of
# showing as source on GitHub. Same syntax as .gitignore, relative to this repo - or,
# strictly, to the cohort's copy, so a release that renames a path with cohort_dest_path
# needs the pattern written the way the COHORT repo has it. Anything unmatched stays
# exactly as it is today: enrolled students open it on GitHub. A deck's
# <name>_files/ bundle follows its deck. solution/, tests/, grading files and .env are
# never copied whatever is written here. Applies to every cohort of this course. Edit,
# then press Sync site (or wait for the next release / daily sync) - a file that does not
# parse stops the sync and is reported, rather than quietly publishing nothing. Full rules:
# https://github.com/{CENTRAL}/blob/main/docs/11-configure-cohort-site.md
"""
_RENAMED_PUBLISH_WORDS = (
    ("cohort site hosts", "semester site hosts"),
    ("cohort's copy", "semester's copy"),
    ("cohort_dest_path", "semester_dest_path"),
    ("COHORT repo", "SEMESTER repo"),
    ("every cohort of", "every semester of"),
)
# The old rule in a header someone has edited: listed by line, never rewritten.
_OLD_PUBLISH_RULE = re.compile(
    r"(?i)the way the (?:semester|cohort) repo has it|to the (?:semester|cohort)'s copy"
)


def old_publish_headers() -> list[list[str]]:
    """The old seeded `publish.yml` header's lines, in each spelling a live repo has."""
    renamed_header = OLD_PUBLISH_HEADER
    for old, new in _RENAMED_PUBLISH_WORDS:
        renamed_header = renamed_header.replace(old, new)
    return [h.rstrip("\n").split("\n") for h in (OLD_PUBLISH_HEADER, renamed_header)]


def publish_header(text: str) -> str:
    """`publish.yml` with its header, where still exactly as seeded, the current one."""
    for old in old_publish_headers():
        text = _replace_block(text, old, PUBLISH_HEADER)
    return text


def old_rule_lines(text: str) -> list[int]:
    """The line numbers of `text` that still state the old rule."""
    return [
        n
        for n, line in enumerate(text.split("\n"), 1)
        if _OLD_PUBLISH_RULE.search(line)
    ]
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


def _rename(org: str, old: str, new: str, description: str | None = None) -> bool:
    """Rename `org/old` to `new`, and - in the same PATCH - bring its description to the
    current wording when it still carries a superseded one."""
    fields = ["-f", f"name={new}"]
    if description:
        fields += ["-f", f"description={description}"]
    code, out = gh("api", "--method", "PATCH", f"repos/{org}/{old}", *fields)
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
    # Runs whenever a step before it has work (the re-render: see RERUN_NOTE), so the
    # plan never calls it "already migrated" then.
    after_work: bool = False
    # Lines for a person to act on by hand, shown in the plan whether or not the step has
    # work (a done step says "already migrated" and would otherwise say nothing).
    notes: Callable[[], list[str]] = list


def run(org: str, steps: list[Step], preview: bool, pause: Pause) -> int:
    """Print the plan; then (not in preview) do, verify, stop at the first failure.

    From the moment the pause is reached until the unpause is verified, ANY way out - a
    failed step, an error, a Ctrl-C - puts back the course repos a semester's pause
    switched off (every semester shares them; not on hold) and names the repos whose
    Actions are still off and where their earlier settings are recorded. The names come
    from what the pause recorded, not from a fresh read, so they are there even when
    GitHub is not."""
    paused = finished = False
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
        pending = False  # a work step above has work
        for step in steps:
            if step.after_work and pending:
                log(f"  {step.name}: runs after the steps above")
            elif planned(step):
                log(f"  {step.name}: already migrated")
                for line in step.notes():
                    log(f"    - {line}")
                continue
            else:
                log(f"  {step.name}:")
            pending = pending or not step.bracket
            for line in [*step.plan(), *step.notes()]:
                log(f"    - {line}")
        if preview:
            log_ok("PREVIEW - nothing was written. Run again with --no-preview.")
            return 0
        held = pause.held()
        if pause.record() is not None:
            # A run that stopped paused, or a hold: Actions are off from here on.
            paused = True
            pause.load()
        if idle and not settle(pause.live_targets, before_switch=False):
            return 1  # the pause step, which waits on its own, is skipped
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
            log_ok(f"{step.name}: done and verified")
            if step.bracket == "unpause":
                paused = False
                if not held:
                    pause.catch_up()
        finished = True
    except Exception as exc:
        log_err(f"{step.name}: stopped by an error - {exc}")
        return 1
    finally:
        # `saved` is filled only once the settings are recorded, the moment before any
        # repo is switched off - so a stop before that says nothing it need not.
        if paused and pause.saved and not finished:
            log_err(pause.stopped())
    if held:
        log_ok(f"{org} is migrated - on hold: {pause.hold_note()}")
    else:
        log_ok(f"{org} is migrated")
    return 0


OFF = {"enabled": False, "allowed_actions": None}
PAUSE_RECORD = records.path("migration_pause")
PAUSE_COMMIT = "migrate: record the Actions settings paused"


MEMBERSHIP_WORKFLOW = "sync-membership.yml"


@dataclass(frozen=True)
class Tick:
    """One dispatch the pause dropped, sent again after the unpause: a `repository_dispatch`
    into the course's `.github` with the payload its own driver sends, marked as the
    migration's (`MIGRATE_DRIVER`)."""

    event: str
    workflow: str
    payload: tuple[str, ...]  # gh api field flags, `-f`/`-F` then `key=value`
    what: str


def lost_ticks(semester_org: str | None) -> list[Tick]:
    """The Scheduled release and the Sync membership a pause drops: for one semester, scoped
    to it as its `semester-config` push is; for a course, what the ds01 timers send - every
    semester."""
    driver = ("-f", f"client_payload[driver]={MIGRATE_DRIVER}")
    if semester_org:
        who = ("-f", f"client_payload[semester_org]={semester_org}")
        return [
            Tick(
                "scheduled-release",
                cadence.WORKFLOW_FILE,
                (*who, *driver),
                f"Scheduled release for {semester_org}",
            ),
            Tick(
                "sync-membership",
                MEMBERSHIP_WORKFLOW,
                (*who, *driver),
                f"Sync membership for {semester_org}",
            ),
        ]
    return [
        Tick(
            "scheduled-release",
            cadence.WORKFLOW_FILE,
            driver,
            "Scheduled release for every semester",
        ),
        Tick(
            "sync-membership",
            MEMBERSHIP_WORKFLOW,
            ("-F", "client_payload[all_semesters]=true", *driver),
            "Sync membership for every semester",
        ),
    ]


def _runs_url(course_org: str, workflow: str) -> str:
    """Where a dispatched run shows up: a `repository_dispatch` answers with no run id."""
    return (
        f"https://github.com/{course_org}/.github/actions/workflows/{workflow}"
        "?query=event%3Arepository_dispatch"
    )


# The scheduled starts a switch must not land just before, as `minute hour` (UTC): the
# ds01 timers on the quarter hour (ds01-infra, not rendered here) and every cron the
# toolkit renders - the Scheduled release at 7 past, the daily Refresh, Publish, Sync
# membership and Sync site, a site's monthly rebuild (taken as daily). Held to the
# rendered crons by `tests/test_migrate.py`. A switch due within TICK_MARGIN seconds of
# one waits past it, then for the runs it started.
TICK_CRONS = (
    "0,15,30,45 *",
    "7,22,37,52 *",
    "27 5",
    "58 5",
    "13 6",
    "41 6",
    "0 5",
)
TICK_MARGIN = 60


def _cron_field(text: str, top: int) -> list[int]:
    return list(range(top)) if text == "*" else [int(x) for x in text.split(",")]


# Each tick as seconds into the UTC day.
TICK_SECONDS = sorted(
    {
        hour * 3600 + minute * 60
        for spec in TICK_CRONS
        for minute in _cron_field(spec.split()[0], 60)
        for hour in _cron_field(spec.split()[1], 24)
    }
)
# How long a real run waits for the runs in flight to finish, and how often it looks.
QUIET_WAIT = 4 * 60
QUIET_POLL = 15
# How long the catch-up waits for the runs it dispatched: the next org's migration (the
# runbook runs course -> semester -> semester back to back) waits only QUIET_WAIT for
# quiet in the course's `.github` before it pauses.
CATCH_UP_WAIT = 10 * 60


def next_tick(at: float) -> float:
    """Seconds from `at` (epoch seconds) to the next tick."""
    into = at % 86400
    return min((tick - into) % 86400 for tick in TICK_SECONDS)


def clear_of_tick(targets: Callable[[], list[tuple[str, str]]]) -> bool:
    """Asked IMMEDIATELY before a switch (the poll before it takes dozens of calls, and a
    tick can fire meanwhile): within TICK_MARGIN of a tick, wait past it and settle again."""
    while (left := next_tick(clock())) < TICK_MARGIN:
        log(f"  a scheduled tick is due in {left:.0f} s - waiting past it")
        sleep(left + QUIET_POLL)
        if not settle(targets, before_switch=True):
            return False
    return True


def settle(
    targets: Callable[[], list[tuple[str, str]]], *, before_switch: bool
) -> bool:
    """Wait until no run is queued or going in `targets` (nor a central deploy), looking
    every QUIET_POLL seconds for up to QUIET_WAIT; with `before_switch`, never ending
    within TICK_MARGIN seconds before a tick (it waits past the tick instead). False, the
    runs named, when they are still going at the end. Only a real run waits: a preview
    writes nothing."""
    deadline = clock() + QUIET_WAIT
    while True:
        left = next_tick(clock())
        if before_switch and left < TICK_MARGIN:
            log(f"  a scheduled tick is due in {left:.0f} s - waiting past it")
            sleep(left + QUIET_POLL)
            deadline = clock() + QUIET_WAIT
            continue
        alive = _alive(targets())
        if not alive:
            return True
        if clock() >= deadline:
            log_err(
                f"a workflow run is queued or running in {', '.join(alive)} (waited "
                f"{QUIET_WAIT // 60} minutes) - wait for it to finish, then re-run the "
                f"migration"
            )
            return False
        log(f"  waiting for the run(s) in {', '.join(alive)} to finish")
        sleep(QUIET_POLL)


class Pause:
    """The migration's pause of one org: every repo whose workflows act on it switched
    off, and - in `<org>/.github/.system/migration-pause.json`, written BEFORE anything is
    switched - what each was set to, so the unpause restores exactly that and a run that
    stops (or is interrupted) can always be resumed or released by hand.

    The record is `{"hold": bool, "repos": {"org/repo": setting}}`. A HOLD (`--hold`) is
    one record per org of a course, each for the org's own repos, written for the window
    in which the tier moves: a migration then never switches anything and never unpauses;
    its bracket steps only mark the record (`migrated`), and `--release` restores."""

    def __init__(
        self,
        org: str,
        targets: Callable[[], list[tuple[str, str]]],
        course_org: str = "",
        semester_org: str | None = None,
    ) -> None:
        self.org, self.targets = org, targets
        self.course_org, self.semester_org = course_org or org, semester_org
        self.saved: dict[str, dict] = {}  # "org/repo" -> its setting before the pause

    def data(self) -> dict | None:
        text = get_file_content(self.org, ".github", PAUSE_RECORD)
        return json.loads(text) if text else None

    def record(self) -> dict[str, dict] | None:
        data = self.data()
        return None if data is None else dict(data.get("repos") or {})

    def held(self) -> bool:
        return bool((self.data() or {}).get("hold"))

    def window_open(self) -> bool:
        """A record that says this org's migration has not finished inside its window: a
        run that stopped paused, or a hold its migration has not completed."""
        data = self.data()
        return data is not None and not (data.get("hold") and data.get("migrated"))

    def load(self) -> None:
        """Remember the recorded repos, so a stop can name them even if GitHub can't
        be read by then."""
        self.saved = self.record() or self.saved

    def write(self, data: dict) -> bool:
        body = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
        return move_files(
            self.org, ".github", {}, PAUSE_COMMIT, files={PAUSE_RECORD: body}
        )

    @staticmethod
    def _live(key: str) -> tuple[str, str]:
        """A recorded repo under its name now (the rename step may have moved it)."""
        org, repo = key.split("/", 1)
        new = REPO_RENAMES.get(repo)
        if new and repo not in _listing(org) and new in _listing(org):
            return org, new
        return org, repo

    def live_targets(self) -> list[tuple[str, str]]:
        """The repos this run waits on: what it pauses, or what its record holds."""
        saved = self.record()
        return self.targets() if saved is None else [self._live(k) for k in saved]

    def release_command(self) -> str:
        return f"python -m dsl_course.migrate --release {self.course_org} --no-preview"

    def hold_note(self) -> str:
        return (
            f"Actions stay off in every org of {self.course_org} until "
            f"`{self.release_command()}`"
        )

    def hint(self, restored: list[str] | None = None) -> str:
        restored = restored or []
        off = [k for k in self.saved if k not in restored]
        repos = ", ".join(off) or f"(see {self.org}/.github/{PAUSE_RECORD})"
        back = (
            f"Actions are back on in the course's repos, which every semester shares: "
            f"{', '.join(restored)}. "
            if restored
            else ""
        )
        return (
            f"{back}Actions are still DISABLED in: {repos}. Their settings before the "
            f"pause are in {self.org}/.github/{PAUSE_RECORD}. Re-run the migration with "
            f"--no-preview to finish (or to restore them), or restore each by hand: "
            f"gh api -X PUT repos/<org>/<repo>/actions/permissions -F enabled=true"
        )

    def stopped(self) -> str:
        """What a stop inside the window says - after putting the course's repos back as
        recorded when this is a semester's pause (they serve every semester of the
        course; the semester's own stay off). Nothing is restored on hold."""
        try:
            if self.held():
                return (
                    f"{self.org} is on hold: {self.hold_note()}. Re-run the migration "
                    f"with --no-preview to finish."
                )
            restored = self.restore_shared()
        except Exception as exc:
            return f"{self.hint()} (could not put the course's repos back: {exc})"
        return self.hint(restored)

    def restore_shared(self) -> list[str]:
        """The course's recorded repos put back as they were, when this pauses a
        semester (the course's own pause has none to share). Returns the ones put back."""
        return [
            key
            for key, state in self.saved.items()
            if key.split("/", 1)[0] == self.course_org != self.org
            and _set_actions(*self._live(key), state)
        ]

    # the pause step ---------------------------------------------------------
    def disabled(self) -> bool:
        """Recorded, and every recorded repo off. An org with no workflow repo records
        `{}`: nothing to switch off is paused."""
        saved = self.record()
        return saved is not None and not any(
            _actions_enabled(*self._live(k)) for k in saved
        )

    def pause(self) -> bool:
        """Wait for quiet, THEN record, THEN switch off: a run switched off mid-flight
        loses the jobs it has not started yet. On hold, Actions are already off: the run
        waits for quiet and marks the window open."""
        data = self.data()
        if data is not None and data.get("hold"):
            self.saved = dict(data.get("repos") or {})
            return settle(self.live_targets, before_switch=False) and self.write(
                {**data, "migrated": False}
            )
        if not settle(self.live_targets, before_switch=True):
            return False
        saved = self.record()
        if saved is None:
            saved = {f"{o}/{r}": _actions_state(o, r) for o, r in self.targets()}
            if not self.write({"hold": False, "repos": saved}):
                return False
        self.saved = saved
        return clear_of_tick(self.live_targets) and self.switch_off()

    def switch_off(self) -> bool:
        return all(_set_actions(*self._live(k), OFF) for k in self.saved)

    def paused(self) -> bool:
        """Every recorded repo off, and no run still going: one that slipped in during the
        switch can start no new job, so it is waited for rather than failing the step."""
        if not self.disabled():
            log_err("Actions did not read back as disabled")
            return False
        return settle(self.live_targets, before_switch=False)

    # the unpause step --------------------------------------------------------
    def restore(self) -> bool:
        """Restore the recorded settings; on hold, only mark this org migrated."""
        data = self.data()
        if data is None:
            return True
        if data.get("hold"):
            self.saved = dict(data.get("repos") or {})
            return bool(data.get("migrated")) or self.write({**data, "migrated": True})
        return self.release()

    def release(self) -> bool:
        """Every recorded repo back as it was, then the record deleted."""
        saved = self.record()
        if saved is None:
            return True
        self.saved = saved
        if not all(_set_actions(*self._live(k), state) for k, state in saved.items()):
            return False
        return move_files(self.org, ".github", {}, PAUSE_COMMIT, delete=[PAUSE_RECORD])

    def restored(self) -> bool:
        data = self.data()
        if data is not None:
            return bool(data.get("hold") and data.get("migrated"))
        for key, want in self.saved.items():
            got = _actions_state(*self._live(key))
            if got["enabled"] != want["enabled"] or (
                want["enabled"] and got["allowed_actions"] != want["allowed_actions"]
            ):
                log_err(f"{key}: Actions did not come back as they were ({want})")
                return False
        return True

    # after the unpause ---------------------------------------------------------
    def catch_up(self) -> None:
        """Dispatch what the pause dropped, say where each run shows up, then wait for
        those runs (`await_catch_up`), so the next org's migration starts clear. Never a
        failure of the migration: the org is migrated by now, and the next tick (at most
        15 minutes, the next hour for membership) covers a miss."""
        since = datetime.fromtimestamp(clock(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sent = 0
        for tick in lost_ticks(self.semester_org):
            url = _runs_url(self.course_org, tick.workflow)
            code, out = gh(
                "api",
                "--method",
                "POST",
                f"repos/{self.course_org}/.github/dispatches",
                "-f",
                f"event_type={tick.event}",
                *tick.payload,
            )
            if code == 0:
                sent += 1
                log_ok(f"dispatched {tick.what}: {url}")
            else:
                log_err(
                    f"could not dispatch {tick.what} ({out[:200]}) - the next tick "
                    f"catches up; the runs: {url}"
                )
        if sent:
            try:
                self.await_catch_up(sent, since)
            except RuntimeError as exc:
                log_err(f"could not follow the catch-up run(s): {exc}")

    def await_catch_up(self, count: int, since: str) -> None:
        """Wait, up to CATCH_UP_WAIT, until the `count` runs dispatched at `since` have
        started in the course's `.github` and none is still going. A `repository_dispatch`
        answers with no run id: the runs are the dispatched ones created since then."""
        query = f"event=repository_dispatch&created=%3E%3D{since}"
        where = f"{self.course_org}/.github"
        log(
            f"  waiting for the catch-up run(s) in {where} to finish (up to "
            f"{CATCH_UP_WAIT // 60} minutes), so the next migration starts clear"
        )
        deadline = clock() + CATCH_UP_WAIT
        while True:
            started = _run_count(self.course_org, ".github", query)
            going = sum(
                _run_count(self.course_org, ".github", f"{query}&status={state}")
                for state in LIVE_RUN_STATES
            )
            if started >= count and not going:
                log_ok(f"the catch-up run(s) in {where} finished")
                return
            if clock() >= deadline:
                log(
                    f"  the catch-up run(s) in {where} are still going after "
                    f"{CATCH_UP_WAIT // 60} minutes - the next migration waits for them "
                    f"before it pauses"
                )
                return
            sleep(QUIET_POLL)

    def steps(self) -> tuple[Step, Step]:
        names = lambda: ", ".join(f"{o}/{r}" for o, r in self.targets())
        on_hold = f"on hold: Actions are already off in every org of {self.course_org}"

        def pause_plan() -> list[str]:
            if self.held():
                return [f"{on_hold}; wait for quiet, mark the window open"]
            return [
                (
                    "wait until no run is queued or running (up to "
                    f"{QUIET_WAIT // 60} minutes), and past a tick due within "
                    f"{TICK_MARGIN} s"
                ),
                f"record the Actions settings in {self.org}/.github/{PAUSE_RECORD}",
                f"disable Actions in {names()}",
            ]

        def unpause_plan() -> list[str]:
            if self.held():
                return [
                    (
                        f"on hold: nothing is unpaused; mark {self.org} migrated in "
                        f"the record. Then `{self.release_command()}`"
                    )
                ]
            return [
                f"restore the recorded Actions settings in {names()}",
                *(
                    f"dispatch {t.what} in {self.course_org}/.github (the tick the "
                    f"pause dropped)"
                    for t in lost_ticks(self.semester_org)
                ),
                (
                    f"wait for those runs to finish (up to {CATCH_UP_WAIT // 60} "
                    "minutes)"
                ),
            ]

        pause = Step(
            "pause automation",
            done=self.disabled,  # never asked: `run` decides the pause by `idle`
            plan=pause_plan,
            do=self.pause,
            verify=self.paused,
            rollback="restore Actions in each repo named below",
            bracket="pause",
        )
        unpause = Step(
            "unpause automation",
            done=lambda: not self.window_open(),
            plan=unpause_plan,
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


def _retired(org: str, repo: str, paths: tuple[str, ...]) -> list[str]:
    """`repo/path (retired: deleted)` for each of `paths` still in `org/repo`: what the
    re-render deletes, listed in the plan beside what it writes."""
    live = _files(org, repo)
    return [f"{repo}/{p} (retired: deleted)" for p in paths if p in live]


def _no_drift(drift: list[str], *, quiet: bool = False) -> bool:
    """The re-render's verify: nothing left differing, else each file named (unless
    `quiet`)."""
    if drift and not quiet:
        log_err(
            f"{len(drift)} file(s) still differ from this checkout: {', '.join(drift)}"
        )
    return not drift


# A re-render also writes what no file shows (a repo secret, a label, a description), so
# its verify cannot read all of it back. A pause record left by a stopped run (or a hold
# this org's migration has not completed) means that run may have stopped at the
# re-render: inside the window it is never "already migrated", and it runs again - it is
# idempotent. Outside the window, done IS the verify.
RERUN_NOTE = "run it again: the pause record says the migration has not finished inside the window"


def _schedule_clean(
    text: str | None, instance: str | None = None, *, quiet: bool = False
) -> bool:
    """`schedule.yml` (beside its `assignments.yml`, `instance`) read by this engine with
    no NOT_MIGRATED fault; each one found is named (unless `quiet`), for a person to fix
    by hand."""
    if text is None:
        return True
    sched = schedule.parse(
        load_yaml_lines(text) or {}, settings.parse_instance(instance)
    )
    bad = [f for f in sched.faults if f.code == NOT_MIGRATED]
    bad += [d for d in sched.dropped if NOT_MIGRATED in d]
    for fault in [] if quiet else bad:
        log_err(
            f"{schedule.SCHEDULE_PATH}: {getattr(fault, 'what', fault)} - fix by hand"
        )
    return not bad


def _instance_clean(text: str) -> bool:
    """An `assignments.yml` this engine reads whole: YAML, and no line it refuses."""
    faults = settings.parse_instance(text).faults
    for fault in faults:
        log_err(f"{ASSIGNMENTS_FILE} {fault.label}: {fault.what} - fix by hand")
    return not faults


def _diff(name: str, old: str, new: str) -> list[str]:
    """The changed lines of `name`, as the plan shows them."""
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", n=0, lineterm=""
    )
    return [f"  {line}" for line in lines if not line.startswith(("---", "+++"))]


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
        self.pause = Pause(org, self.targets, course_org, org)
        self.renamed_now: dict[str, str] = {}  # old -> new, renamed by this run

    def config(self) -> str:
        """The config repo under whichever name it has right now."""
        return CONFIG_REPO if CONFIG_REPO in _listing(self.org) else OLD_CONFIG_REPO

    def join(self) -> str:
        return JOIN_REPO if JOIN_REPO in _listing(self.org) else OLD_JOIN_REPO

    def own_targets(self) -> list[tuple[str, str]]:
        """The semester's own repos that carry a workflow."""
        own = [self.config(), self.join(), pages_repo(self.org), ".github"]
        return _with_workflows(self.org, own)

    def targets(self) -> list[tuple[str, str]]:
        """Every repo whose workflows act on this semester: its own, and the course's."""
        return self.own_targets() + Course(self.course).targets()

    # rename -------------------------------------------------------------------
    def renames_left(self) -> dict[str, str]:
        names = set(_listing(self.org))
        return {old: new for old, new in REPO_RENAMES.items() if old in names}

    def description(self, repo: str) -> str | None:
        """The current wording for `repo`'s description, when it still says an old one."""
        row = _listing(self.org).get(repo) or {}
        return current_description(row.get("description") or "", "semester")

    def rename_plan(self) -> list[str]:
        out = []
        for old, new in self.renames_left().items():
            out.append(f"rename {old} -> {new}")
            if want := self.description(old):
                out.append(f"  description -> {want}")
        return out

    def rename(self) -> bool:
        names = set(_listing(self.org))
        for old, new in self.renames_left().items():
            if new in names:
                log_err(f"{self.org} has both {old} and {new} - resolve by hand")
                return False
            if not _rename(self.org, old, new, self.description(old)):
                return False
            self.renamed_now[old] = new
        return True

    def renamed(self) -> bool:
        """No repo left under an old name. A repo that never existed (a semester with
        no `welcome`) has nothing to rename."""
        return not self.renames_left()

    def rename_verified(self) -> bool:
        """Renamed, each old name this run renamed redirecting to its new one, and each
        description in the current wording."""
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
        worded = [n for n in self.renamed_now.values() if self.description(n)]
        for new in worded:
            log_err(f"{self.org}/{new}: the description still has its old wording")
        return not stale and not worded

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
        new = seeded_yaml(fix_header(new, ref), ref, self.org)
        try:
            return new if same_people(old, new) else None
        except yaml.YAMLError:
            return None

    def text_files(self) -> list[tuple[str, str, bool]]:
        """`(repo, path, is YAML)` for each seeded text the layout step rewrites."""
        return [
            (self.config(), schedule.SCHEDULE_PATH, True),
            (self.config(), INSTRUCTORS_FILE, True),
            (self.join(), JOIN_README, False),
            (".github", PROFILE_README, False),
        ]

    def texts(self) -> dict[tuple[str, str], tuple[str, str]]:
        """`{(repo, path): (text now, text after the layout step)}` for each seeded text
        that is there."""
        ref = central_ref_for(self.course)
        out = {}
        for repo, path, is_yaml in self.text_files():
            text = get_file_content(self.org, repo, path)
            if text is not None:
                out[repo, path] = (
                    text,
                    seeded_yaml(text, ref, self.org)
                    if is_yaml
                    else renamed(text, self.org),
                )
        return out

    def text_work(self) -> dict[str, dict[str, bytes]]:
        """`{repo: {path: new bytes}}` for each seeded text the layout still rewrites."""
        out: dict[str, dict[str, bytes]] = {}
        for (repo, path), (text, new) in self.texts().items():
            if new != text:
                out.setdefault(repo, {})[path] = new.encode()
        return out

    def outcome_work(self, live: dict[str, str]) -> dict[str, str]:
        """`{path now: path under the new op id}` for each outcome record of a renamed op,
        wherever the layout's moves put it first."""
        moves = fold(set(live), SEMESTER_MOVES)
        out = {}
        for path in sorted(live):
            new = renamed_outcome(moves.get(path, path))
            if new:
                out[path] = new
        return out

    def layout_work(self) -> tuple[dict, dict, list, bool]:
        """(moves, files, deletes, old pointer present) for semester-config."""
        repo = self.config()
        live = _files(self.org, repo)
        moves = fold(set(live), SEMESTER_MOVES)
        deletes = sorted(p for p in live if p.endswith(SAMPLE_SUFFIX))
        files: dict[str, bytes] = {}
        for old, new in self.outcome_work(live).items():
            # Rewritten, not moved: the record names its op. One this engine already
            # wrote under the new id is the newer record, and is kept.
            moves.pop(old, None)
            deletes.append(old)
            if new not in live:
                text = get_file_content(self.org, repo, old) or ""
                files[new] = outcome_text(text, Path(new).stem)
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
        return not (moves or files or deletes or old_pointer or self.text_work())

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
        outcomes = self.outcome_work(_files(self.org, repo))
        if outcomes:
            out.append(
                f"rename {len(outcomes)} console outcome record(s) to the new op id"
            )
            out += [f"  {old} -> {new}" for old, new in outcomes.items()]
        samples = [p for p in deletes if p.endswith(SAMPLE_SUFFIX)]
        if samples:
            out.append(f"delete {len(samples)} *{SAMPLE_SUFFIX} file(s)")
            out += [f"  {p}" for p in samples]
        if old_pointer:
            out.append(f"delete {OLD_POINTER_REPO}/{COURSE_CONFIG} (the old pointer)")
        texts = self.texts()
        work = self.text_work()
        if work:
            out.append("old repo names and seeded wording rewritten in:")
            out += [
                f"  {REPO_RENAMES.get(r, r)}/{path}"
                for r, paths in work.items()
                for path in paths
            ]
        final = {
            f"{REPO_RENAMES.get(r, r)}/{p}": new for (r, p), (_, new) in texts.items()
        }
        if files.get(INSTRUCTORS_FILE):
            final[f"{CONFIG_REPO}/{INSTRUCTORS_FILE}"] = files[
                INSTRUCTORS_FILE
            ].decode()
        return out + review_plan(final)

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
        text = self.text_work()
        if not move_files(
            self.org,
            repo,
            moves,
            LAYOUT_COMMIT,
            files={**text.pop(repo, {}), **files},
            delete=deletes,
        ):
            return False
        if (old_pointer or OLD_POINTER_REPO in text) and not move_files(
            self.org,
            OLD_POINTER_REPO,
            {},
            LAYOUT_COMMIT,
            files=text.pop(OLD_POINTER_REPO, {}),
            delete=[COURSE_CONFIG] if old_pointer else [],
        ):
            return False
        return all(
            move_files(self.org, other, {}, LAYOUT_COMMIT, files=written)
            for other, written in text.items()
        )

    def layout_verified(self) -> bool:
        repo = self.config()
        live = _files(self.org, repo)
        before = getattr(self, "before", live)
        # Every marker at its new path, byte for byte, and nowhere else. An outcome record
        # of a renamed op is rewritten (it names its op): it must be at its new path.
        renamed = self.outcome_work(before)
        for old, new in renamed.items():
            if old in live or new not in live:
                log_err(f"{repo}: an outcome record did not arrive at {new}")
                return False
        for old, new in fold(set(before), SEMESTER_MOVES).items():
            if old in renamed:
                continue
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
        if text is None:
            return None, None
        return text, schedule_timing(schedule_keys(text))

    def instance_text(self) -> str | None:
        return get_file_content(self.org, self.config(), ASSIGNMENTS_FILE)

    def templates_run_keys(self, meta: dict) -> dict[str, dict]:
        """`{template: its run keys}` for each template the schedule names: the course's
        record of what its template-keys step took out, else the template itself."""
        record = json.loads(
            get_file_content(self.course, ".github", RUN_KEYS_RECORD) or "{}"
        )
        entries = meta.get("assignments")
        out = {}
        for entry in entries.values() if isinstance(entries, dict) else ():
            repo = str((entry or {}).get("course_source_repo") or "")
            if repo and repo not in out:
                out[repo] = record.get(repo) or template_run_keys(
                    get_file_content(
                        self.course, repo, GRADING_FILE, ref=SOLUTION_BRANCH
                    )
                )
        return out

    def instance_work(self) -> tuple[str | None, list[str]]:
        """The `assignments.yml` this semester needs (None: nothing to write), and the
        plan's notes. Absent, it is seeded from the skeleton; either way it gains what
        `instance_additions` finds in the OLD schedule and its templates."""
        text, _ = self.keys_text()
        current = self.instance_text()
        if current is not None and (text is None or text == self.keys_text()[1]):
            return None, []
        meta = _yaml(text)
        existing = settings.parse_instance(current)
        lower = [
            ("semester", existing.defaults),
            ("course", settings.course_defaults(self.course)),
            ("institution", settings.institution_defaults()),
        ]
        additions, notes = instance_additions(
            meta, self.templates_run_keys(meta), lower, existing
        )
        base = current
        if base is None:
            ref = central_ref_for(self.course)
            base = semester_scaffold(self.org, ASSIGNMENTS_FILE, ref)
        new = with_instance_keys(base, additions) if additions else base
        return (None if new == current else new), notes

    def keys_done(self) -> bool:
        """The schedule holds only this engine's keys and reads clean, and the semester
        has its `assignments.yml`. A key the line rewrite cannot reach (inside a flow
        mapping, say) keeps this undone: the step then stops, naming the entry to fix by
        hand."""
        if not self.renamed():
            return False
        text, new = self.keys_text()
        return (
            text == new
            and self.instance_text() is not None
            and _schedule_clean(text, self.instance_text(), quiet=True)
        )

    def keys_plan(self) -> list[str]:
        text, new = self.keys_text()
        instance, notes = self.instance_work()
        out = [
            (
                f"{CONFIG_REPO}/{schedule.SCHEDULE_PATH}: cohort_dest_* -> "
                f"semester_dest_*, type -> kind on releases and events; the keys that "
                f"left it removed (assignment title, grading_datetime, "
                f"semester_dest_repo; a release's assignment; enrolment)"
            ),
            *_diff(schedule.SCHEDULE_PATH, text or "", new or ""),
        ]
        if instance is not None:
            current = self.instance_text()
            seeded = current is None
            if seeded:
                ref = central_ref_for(self.course)
                current = semester_scaffold(self.org, ASSIGNMENTS_FILE, ref)
            out.append(
                f"{CONFIG_REPO}/{ASSIGNMENTS_FILE}: "
                + ("seed the skeleton, and " if seeded else "")
                + "write what the old files said"
            )
            out += _diff(ASSIGNMENTS_FILE, current, instance)
        return out + notes

    def keys(self) -> bool:
        text, new = self.keys_text()
        instance, _ = self.instance_work()
        files = {}
        if text != new:
            files[schedule.SCHEDULE_PATH] = new.encode()
        if instance is not None:
            if not _instance_clean(instance):
                return False
            files[ASSIGNMENTS_FILE] = instance.encode()
        if not files:
            return True
        # One commit: the schedule loses `grading_datetime` and `semester_dest_repo` in
        # the same write that puts them into assignments.yml.
        return move_files(self.org, self.config(), {}, KEYS_COMMIT, files=files)

    def keys_verified(self) -> bool:
        text, new = self.keys_text()
        instance = self.instance_text()
        return (
            text == new
            and instance is not None
            and _instance_clean(instance)
            and _schedule_clean(text, instance)
        )

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
        return _drift(self.org, wanted) + _retired(
            self.org, JOIN_REPO, RETIRED_JOIN_FORMS
        )

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
        return not self.pause.window_open() and self.rerendered(quiet=True)

    def rerender(self) -> bool:
        ref = central_ref_for(self.course)
        # The lock first: the Join team form is rendered from it, and the verify renders
        # from the synced lock.
        failures = 0 if sync_team_lock(self.course, self.org).ok else 1
        failures += refresh_join_workflows(self.org)
        failures += refresh_config_system_files(self.org, ref)
        failures += refresh_semester_pointer(self.org, self.course)
        failures += update_profile_readme(self.org, central_ref=ref)
        return failures == 0

    def steps(self) -> list[Step]:
        repo = f"{self.org}/{CONFIG_REPO}"
        return [
            self.pause.steps()[0],
            Step(
                "rename repos",
                done=self.renamed,
                plan=self.rename_plan,
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
                rollback=(
                    f"git revert the '{LAYOUT_COMMIT}' commit(s) in {repo}, "
                    f"{self.org}/.github and {self.org}/{JOIN_REPO}"
                ),
            ),
            Step(
                "keys",
                done=self.keys_done,
                plan=self.keys_plan,
                do=self.keys,
                verify=self.keys_verified,
                rollback=(
                    f"git revert the '{KEYS_COMMIT}' commit in {repo} (it holds "
                    f"{schedule.SCHEDULE_PATH} and {ASSIGNMENTS_FILE})"
                ),
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
                        *([RERUN_NOTE] if self.pause.window_open() else []),
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
                after_work=True,
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
        """`{template: its rewritten grading_config.yml}` for each that needs it: the
        `format:` spelling, and the run settings out (they are each semester's now)."""
        out = {}
        for repo in self.templates():
            text = self.grading_text(repo)
            new = (
                strip_run_keys(grading_config_keys(text)) if text is not None else None
            )
            if new is not None and new != text:
                out[repo] = new
        return out

    def run_keys_record(self) -> dict[str, dict]:
        """The record of the run settings taken out so far, and those about to be."""
        record = json.loads(
            get_file_content(self.org, ".github", RUN_KEYS_RECORD) or "{}"
        )
        for repo in self.templates_left():
            found = template_run_keys(self.grading_text(repo))
            if found:
                record[repo] = found
        return record

    def rewrite_templates(self) -> bool:
        # The record FIRST: once a template has lost its run settings, the record is the
        # only place its semesters' keys steps can read them from.
        record = self.run_keys_record()
        if record and not move_files(
            self.org,
            ".github",
            {},
            KEYS_COMMIT,
            files={
                RUN_KEYS_RECORD: (
                    json.dumps(record, indent=2, sort_keys=True) + "\n"
                ).encode()
            },
        ):
            return False
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

    # seeded text -----------------------------------------------------------
    def texts(self) -> dict[tuple[str, str, str], tuple[str, str]]:
        """`{(repo, branch, path): (text now, text after this step)}` for `dsl-course.yml`
        and each live template's `grading_config.yml` (on its solution branch)."""
        ref = central_ref_for(self.org)
        where = [(".github", "", COURSE_CONFIG)]
        where += [(r, SOLUTION_BRANCH, GRADING_FILE) for r in self.templates()]
        out = {}
        for repo, branch, path in where:
            text = get_file_content(self.org, repo, path, ref=branch)
            if text is not None:
                # The registry's old name is the course's own, named in its own file.
                registry = path == COURSE_CONFIG
                out[repo, branch, path] = (
                    text,
                    seeded_yaml(text, ref, self.org, registry=registry),
                )
        return out

    def text_work(self) -> dict[tuple[str, str], dict[str, bytes]]:
        out: dict[tuple[str, str], dict[str, bytes]] = {}
        for (repo, branch, path), (text, new) in self.texts().items():
            if new != text:
                out.setdefault((repo, branch), {})[path] = new.encode()
        return out

    def text_plan(self) -> list[str]:
        where = lambda repo, branch, path: (
            f"{repo}@{branch}/{path}" if branch else f"{repo}/{path}"
        )
        out = [
            f"old repo names and seeded wording rewritten in {where(r, b, p)}"
            for (r, b), files in self.text_work().items()
            for p in files
        ]
        final = {where(*key): new for key, (_, new) in self.texts().items()}
        return out + review_plan(final)

    def rewrite_text(self) -> bool:
        return all(
            move_files(self.org, repo, {}, TEXT_COMMIT, files=files, branch=branch)
            for (repo, branch), files in self.text_work().items()
        )

    # materials ---------------------------------------------------------------
    def materials_repos(self) -> list[str]:
        """The live materials repos: those with the topic, and the ones the topic step
        gives it (so a preview, where that step has not run, plans the same moves)."""
        topicked = [
            repo
            for repo, row in _listing(self.org).items()
            if is_materials_repo(row) and not row.get("archived")
        ]
        return sorted({*topicked, *self.untopicked_materials()})

    def materials_moves(self) -> dict[str, dict[str, str]]:
        return {
            repo: moves
            for repo in self.materials_repos()
            if (moves := fold(set(_files(self.org, repo)), MATERIALS_MOVES))
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

    def untopicked_materials(self) -> list[str]:
        """The `course-materials-*` repos that do not carry the materials topic yet: the
        prefix was how a materials repo was told, the topic is now."""
        return [
            repo
            for repo, row in sorted(_listing(self.org).items())
            if repo.startswith(MATERIALS_REPO_PREFIX)
            and not row.get("archived")
            and not is_materials_repo(row)
        ]

    def topic_materials(self) -> bool:
        return all(
            set_repo_topics(
                self.org,
                repo,
                sorted(
                    {*(_listing(self.org)[repo].get("topics") or []), MATERIALS_TOPIC}
                ),
            )
            for repo in self.untopicked_materials()
        )

    # publish.yml -------------------------------------------------------------
    def publish_texts(self) -> dict[str, tuple[str, str]]:
        """`{materials repo: (its publish.yml now, after this step)}`, for each that has
        one."""
        out = {}
        for repo in self.materials_repos():
            text = get_file_content(self.org, repo, PUBLISH_FILE)
            if text is not None:
                out[repo] = (text, publish_header(text))
        return out

    def publish_work(self) -> dict[str, bytes]:
        return {
            repo: new.encode()
            for repo, (text, new) in self.publish_texts().items()
            if new != text
        }

    def publish_notes(self) -> list[str]:
        """Each `publish.yml` that still states the old rule where it is not as seeded:
        listed by line, for a person to reword."""
        found = {
            repo: lines
            for repo, (_, new) in self.publish_texts().items()
            if (lines := old_rule_lines(new))
        }
        if not found:
            return []
        return [
            (
                f"{PUBLISH_FILE} still says patterns match the semester's copy (they match "
                "SOURCE paths now) - reword by hand, left as it is:"
            ),
            *(
                f"  {repo}/{PUBLISH_FILE}: line(s) {', '.join(map(str, lines))}"
                for repo, lines in found.items()
            ),
        ]

    def rewrite_publish(self) -> bool:
        return all(
            move_files(self.org, repo, {}, TEXT_COMMIT, files={PUBLISH_FILE: body})
            for repo, body in self.publish_work().items()
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
        materials = self.materials_repos()
        for repo in discover_content_repos(self.org):
            wanted[repo] = hosted(repo, RELEASE_WORKFLOWS)
            if repo in materials:
                wanted[repo] |= materials_system_files(self.org, repo)
        for row in templates:
            if not row.get("archived"):
                wanted[row["name"]] = hosted(row["name"], TEMPLATE_WORKFLOWS)
        retired = _retired(self.org, ".github", seed.RETIRED_GITHUB_WORKFLOWS) + [
            line
            for repo in wanted
            if repo != ".github"
            for line in _retired(self.org, repo, RETIRED_WORKFLOWS)
        ]
        return _drift(self.org, wanted) + retired

    def rerendered(self, *, quiet: bool = False) -> bool:
        """The re-render's verify: the registry migrated, and no drift."""
        return self.registry_done() and _no_drift(self.drift(), quiet=quiet)

    def rerender_done(self) -> bool:
        return not self.pause.window_open() and self.rerendered(quiet=True)

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
                    line
                    for r, new in self.templates_left().items()
                    for line in (
                        (
                            f"{r}@{SOLUTION_BRANCH}/{GRADING_FILE}: format: -> formats:, "
                            f"run settings out (recorded in .github/{RUN_KEYS_RECORD} "
                            f"for the semesters' {ASSIGNMENTS_FILE})"
                        ),
                        *_diff(GRADING_FILE, self.grading_text(r) or "", new),
                    )
                ],
                do=self.rewrite_templates,
                verify=self.templates_verified,
                rollback=(
                    f"git revert the '{KEYS_COMMIT}' commit on each template's "
                    f"{SOLUTION_BRANCH} branch"
                ),
            ),
            Step(
                "seeded text",
                done=lambda: not self.text_work(),
                plan=self.text_plan,
                do=self.rewrite_text,
                verify=lambda: not self.text_work(),
                rollback=(
                    f"git revert the '{TEXT_COMMIT}' commit in {dotgithub} and on each "
                    f"template's {SOLUTION_BRANCH} branch"
                ),
            ),
            Step(
                "materials topic",
                done=lambda: not self.untopicked_materials(),
                plan=lambda: [
                    f"{repo}: add the topic {MATERIALS_TOPIC}"
                    for repo in self.untopicked_materials()
                ],
                do=self.topic_materials,
                verify=lambda: not self.untopicked_materials(),
                rollback=f"remove the {MATERIALS_TOPIC} topic from the materials repos",
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
                f"{PUBLISH_FILE} comment",
                done=lambda: not self.publish_work(),
                plan=lambda: [
                    (
                        f"{repo}/{PUBLISH_FILE}: the seeded comment -> the current one "
                        "(patterns match SOURCE paths)"
                    )
                    for repo in self.publish_work()
                ],
                do=self.rewrite_publish,
                verify=lambda: not self.publish_work(),
                rollback=f"git revert the '{TEXT_COMMIT}' commit in each materials repo",
                notes=self.publish_notes,
            ),
            Step(
                "re-render",
                done=self.rerender_done,
                plan=lambda: (
                    [
                        "Refresh actions from this checkout; these files differ now:",
                        *(f"  {path}" for path in self.drift()),
                        *([RERUN_NOTE] if self.pause.window_open() else []),
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
                after_work=True,
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
        if _archived_semester(listing):
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
        # pause is released (no record left, or a hold it has finished inside), and its
        # Actions are on - unless they are off because THIS semester's migration paused
        # them and stopped (its record is there): then this run resumes it; or because
        # the course is on hold, which holds every live semester with it. Another
        # semester's pause in flight shows as the course's Actions off with no record
        # here, and is refused.
        resuming = target.pause.record() is not None
        course_held = parent.pause.held()
        course_on = parent.pause.record() is None and all(
            _actions_enabled(o, r) for o, r in parent.targets()
        )
        if not parent.work_done() or parent.pause.window_open():
            log_err(
                f"{course} is not fully migrated - finish that first: "
                f"`python -m dsl_course.migrate {course} --no-preview`, then this semester"
            )
            return None
        if course_held and not target.pause.held():
            log_err(
                f"{course} is on hold and {org} is not - run `python -m dsl_course."
                f"migrate --hold {course} --no-preview` again (it holds the semesters it "
                f"missed), then this semester"
            )
            return None
        # A stopped semester run puts the course's repos back on (`Pause.stopped`), so the
        # course's Actions alone cannot tell that one is unfinished: its record does.
        others = [
            other
            for other in _registered(course)
            if other != org
            and (data := Pause(other, list).data()) is not None
            and not data.get("hold")
        ]
        if others:
            log_err(
                f"another semester's migration under {course} has not finished "
                f"({', '.join(others)}) - finish it (re-run it with --no-preview), then "
                f"this semester"
            )
            return None
        if not (course_on or resuming or course_held):
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
    # No quiet check here: a preview writes nothing, and a real run waits for quiet
    # before its first write (`settle`).
    return target


def _archived_semester(listing: dict[str, dict]) -> bool:
    """Whether a semester org (its `listing`) is archived, or has no config repo."""
    config = listing.get(CONFIG_REPO) or listing.get(OLD_CONFIG_REPO)
    return (
        config is None
        or bool(config.get("archived"))
        or all(r.get("archived") for r in listing.values())
    )


# ------------------------------------------------------------------ hold
# The tier ref moves while EVERY org it serves is paused: the old engine faults on a
# migrated org and the new one on an unmigrated one, and a Promote refreshes every org on
# `release`. So: `--hold` every real course, Promote once, migrate each course and then
# its semesters (they neither pause nor unpause on hold), then `--release` each.


def _registered(course_org: str) -> list[str]:
    """The semesters the course's registry names, under its old name or its new one."""
    old, new = Course(course_org).registry()
    try:
        data = yaml.safe_load(new if new is not None else old or "")
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"the semester registry of {course_org} is not YAML"
        ) from exc
    if isinstance(data, dict):
        data = data.get("semesters") or data.get("cohorts") or []
    return sorted(str(x) for x in data if x) if isinstance(data, list) else []


def hold_pauses(course_org: str, *, recorded: bool = False) -> list[Pause]:
    """One pause per org of the course, each for the org's own repos: the course first,
    then every live (not archived) semester its registry names - and, with `recorded`,
    every semester the course's hold record names (one archived, or taken out of the
    registry, while the hold stood still holds a record)."""
    course = Pause(course_org, Course(course_org).targets)
    orgs = []
    for org in _registered(course_org):
        listing = _listing(org)
        if ".github" in listing and not _archived_semester(listing):
            orgs.append(org)
    if recorded:
        named = (course.data() or {}).get("semesters") or []
        orgs += [org for org in named if org not in orgs]
    return [course] + [
        Pause(org, Semester(org, course_org).own_targets, course_org, org)
        for org in orgs
    ]


def _course_keys(repos: dict, course_org: str) -> dict:
    return {k: v for k, v in repos.items() if k.split("/", 1)[0] == course_org}


def hold(course_org: str, preview: bool) -> int:
    """Pause the course and every live semester of it, one record per org marked `hold`:
    wait for quiet across all of them, record every org, THEN switch any off, then
    verify. Idempotent: a rerun holds what is not held yet.

    A semester migration in flight (its own, non-hold record) is taken over: the course
    repos it switched off move into the COURSE's hold record with the settings it recorded
    before its pause - read now, they would say "off", and the release would leave them
    off for good. Two records that both hold the course's repos are refused."""
    pauses = hold_pauses(course_org, recorded=True)
    course, semesters = pauses[0], pauses[1:]
    records_now = {p.org: p.data() for p in pauses}
    in_flight = {
        org: _course_keys(data.get("repos") or {}, course_org)
        for org, data in records_now.items()
        if org != course_org and data is not None and not data.get("hold")
    }
    carriers = [org for org, keys in in_flight.items() if keys]
    if len(carriers) > 1 or (carriers and records_now[course_org] is not None):
        both = carriers + ([course_org] if records_now[course_org] is not None else [])
        log_err(
            f"the pause records of {', '.join(both)} both hold {course_org}'s repos - "
            f"finish those migrations (re-run each with --no-preview), then hold"
        )
        return 1
    log_step(f"Hold plan for {course_org}")
    for p in pauses:
        data = records_now[p.org]
        if data is not None and data.get("hold"):
            log(f"  {p.org}: already on hold")
        elif data is not None:
            log(f"  {p.org}: take over the record of its migration in flight")
        else:
            names = (
                ", ".join(f"{o}/{r}" for o, r in p.targets()) or "(no workflow repo)"
            )
            log(
                f"  {p.org}: record the Actions settings, then disable Actions in {names}"
            )
    if preview:
        log_ok("PREVIEW - nothing was written. Run again with --no-preview.")
        return 0
    targets = lambda: [t for p in pauses for t in p.live_targets()]
    if not settle(targets, before_switch=True):
        return 1
    held_orgs = sorted(p.org for p in semesters)
    for p in pauses:
        data = records_now[p.org]
        if data is not None and data.get("hold"):
            new = data
            if p is course and set(held_orgs) - set(data.get("semesters") or []):
                new = {**data, "semesters": sorted({*held_orgs, *data["semesters"]})}
        else:
            if data is not None:
                repos = dict(data.get("repos") or {})
            else:
                repos = {f"{o}/{r}": _actions_state(o, r) for o, r in p.targets()}
            if p is course:
                for keys in in_flight.values():
                    repos |= keys  # their settings before that semester's pause
            else:
                repos = {
                    k: v for k, v in repos.items() if k not in in_flight.get(p.org, {})
                }
            new = {"hold": True, "migrated": False, "repos": repos}
            if p is course:
                new["semesters"] = held_orgs
        if new is not data and not p.write(new):
            log_err(f"could not record the hold of {p.org} - nothing more was switched")
            return 1
        p.saved = dict(new.get("repos") or {})
    if not clear_of_tick(targets):
        return 1
    switched = [p.switch_off() for p in pauses]
    if not all(switched) or not all(p.disabled() for p in pauses):
        log_err(
            "Actions did not read back as disabled everywhere - re-run the hold, or "
            f"`python -m dsl_course.migrate --release {course_org} --no-preview`"
        )
        return 1
    if not settle(targets, before_switch=False):
        return 1
    log_ok(
        f"{course_org} is on hold ({len(pauses)} org(s)). Next: migrate the course, then "
        f"each semester (`--status {course_org}` reads migrated for every org), then "
        f"`python -m dsl_course.migrate --release {course_org} --no-preview`"
    )
    return 0


def release(course_org: str, preview: bool, abandon: bool = False) -> int:
    """End the hold: every held org's Actions back as recorded (the semesters first, the
    course last), then one catch-up dispatch of each tick for every semester. Refused
    while an org is not migrated inside the hold, unless `abandon` (which names them)."""
    held = [p for p in hold_pauses(course_org, recorded=True) if p.held()]
    course = [p for p in held if p.org == course_org]
    held = [p for p in held if p.org != course_org] + course
    log_step(f"Release plan for {course_org}")
    if not held:
        log_ok(f"{course_org} is not on hold - nothing to release")
        return 0
    unmigrated = []
    for p in held:
        data = p.data() or {}
        if not data.get("migrated"):
            unmigrated.append(p.org)
        state = "migrated" if data.get("migrated") else "NOT migrated inside the hold"
        repos = ", ".join(data.get("repos") or {}) or "(none)"
        log(f"  {p.org} ({state}): restore Actions in {repos}")
    log(f"  then dispatch the ticks the hold dropped in {course_org}/.github")
    if unmigrated and not abandon:
        log_err(
            f"not migrated inside the hold: {', '.join(unmigrated)} - migrate each "
            f"(`python -m dsl_course.migrate <org> --no-preview`) and release again, or "
            f"give up the hold without them: `--release {course_org} --abandon "
            f"--no-preview`"
        )
        return 1
    if unmigrated:
        log_err(f"abandoning the hold of {', '.join(unmigrated)} (not migrated)")
    if preview:
        log_ok("PREVIEW - nothing was written. Run again with --no-preview.")
        return 0
    for p in held:
        if not (p.release() and p.restored()):
            log_err(
                f"{p.org}: Actions did not come back as recorded - re-run the release"
            )
            return 1
        log_ok(f"{p.org}: Actions restored")
    Pause(course_org, Course(course_org).targets).catch_up()
    log_ok(f"{course_org} is released")
    return 0


def hold_status(course_org: str) -> int:
    """Who is paused under `course_org`, from each org's record and its repos. Reads only."""
    log_step(f"Pause status of {course_org}")
    for p in hold_pauses(course_org, recorded=True):
        data = p.data()
        if data is None:
            log(f"  {p.org}: not paused")
            continue
        repos = data.get("repos") or {}
        off = sum(not _actions_enabled(*p._live(k)) for k in repos)
        if data.get("hold"):
            kind = "on hold, " + (
                "migrated" if data.get("migrated") else "not migrated yet"
            )
        else:
            kind = "paused by a migration that has not finished"
        log(
            f"  {p.org}: {kind} - Actions off in {off} of {len(repos)} recorded repo(s)"
        )
    return 0


def _is_course(org: str) -> bool:
    listing = _listing(org)
    if COURSE_HUB_TOPIC not in _topics(listing):
        log_err(f"{org} is not a course org - --hold, --release and --status take one")
        return False
    if listing[".github"].get("archived"):
        log_err(f"{org}/.github is archived - an archived course is never touched")
        return False
    return True


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
    parser.add_argument(
        "org",
        help="The course org or semester org to migrate (the course org with --hold, "
        "--release or --status)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--hold",
        action="store_true",
        help="Pause the course and every live semester of it until --release",
    )
    mode.add_argument(
        "--release",
        action="store_true",
        help="Restore every held org's Actions and dispatch the dropped ticks",
    )
    mode.add_argument(
        "--status", action="store_true", help="Print which orgs are paused"
    )
    parser.add_argument(
        "--abandon",
        action="store_true",
        help="With --release: release although an org is not migrated inside the hold",
    )
    add_preview_flag(parser, "Print the plan and write nothing (default).")
    args = parser.parse_args()
    _forget()
    if args.abandon and not args.release:
        parser.error("--abandon goes with --release")
    if args.hold or args.release or args.status:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            log_err(
                "the migration runs from a maintainer's laptop, never from a workflow"
            )
            return 1
        try:
            if not _is_course(args.org):
                return 1
            if args.status:
                return hold_status(args.org)
            if args.hold:
                return hold(args.org, args.preview)
            return release(args.org, args.preview, args.abandon)
        except RuntimeError as exc:
            log_err(f"could not read {args.org}: {exc}")
            return 1
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
