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
dispatches one Scheduled release, one Sync membership and one Sync site in its place, and
waits (bounded) for those runs, so the next org's migration finds the course quiet. For a
real course the tier moves inside a HOLD of every org of the course (`--hold`,
`--release`); a migration under a hold neither pauses nor unpauses. What a step would
refuse is asked in the plan ("will stop here"): the preview fails there, and a run stops
before its pause.

Semester org, in order: preflight, pause, rename repos, layout, keys, schedule shape,
config layers, topic, re-render, status, unpause. Course org: preflight, pause, registry,
.system/, dsl-course.yml keys, template keys, seeded text, template starter (decision 0028:
`starter:` written into each live template's grading_config.yml, read from its answer
markers), materials topic, materials files, public website, assignment topic, config
layers, re-render, status, unpause. The steps before "config layers" bring an org to the
0.10 files (their seeds frozen in `templates/legacy-0.10/`); "config layers" folds those
into one file per level (decision 0039).
"""

from __future__ import annotations

import csv
import difflib
import io
import json
import os
import re
import sys
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from time import time as clock

import yaml

from . import (
    cadence,
    legacy,
    opencourse,
    policy,
    records,
    roster,
    schedule,
    seed,
    settings,
    status,
    teams,
)
from .central import CENTRAL, pin_central_ref
from .config import read_course, read_semester
from .course import (
    CONFIG_REPO,
    COURSE_BLOCK,
    COURSE_HUB_TOPIC,
    DEFAULTS_BLOCK,
    JOIN_REPO,
    MAINTAINING_FILE,
    MATERIALS_REPO_PREFIX,
    MIGRATE_DRIVER,
    OPEN_SITE_BLOCK,
    PEOPLE_BLOCK,
    SEMESTER_FILE,
    SEMESTER_TOPIC,
    SOLUTION_BRANCH,
    STUDENT_SITE_BLOCK,
    SUBMIT_VIA,
    SYLLABUS_SAMPLE_FILE,
    SYLLABUS_SESSIONS_FILE,
    TOOLKIT_REF_KEY,
    course_admins,
    course_block,
    pages_repo,
    semester_of,
)
from .course import COURSE_CONFIG as NEW_COURSE_FILE
from .derive import derivable_sources, starter_mode
from .discovery import (
    SEMESTERS_PATH,
    TEMPLATE_TOPIC,
    central_ref_for,
    discover_content_repos,
    discover_semesters,
    is_assignment_template,
    list_org_repos,
)
from .faults import NOT_MIGRATED, RunSettingInTemplate, Unusable
from .gh_contents import (
    blob_sha,
    get_file_content,
    load_yaml_lines,
    move_files,
    refuse_clashes,
    repo_blob_shas,
    top_level,
)
from .ghcli import forget_all, gh, git
from .grading_spec import GRADING_FILE, assignment_title, parse_grading_spec
from .legacy import OLD_ASSIGNMENTS_FILE as ASSIGNMENTS_FILE
from .legacy import OLD_COURSE_FILE as COURSE_CONFIG
from .legacy import OLD_INSTRUCTORS_FILE as INSTRUCTORS_FILE
from .legacy import OLD_OPENCOURSE_FILE as OPENCOURSE_FILE
from .log import CLIParser, add_preview_flag, log, log_err, log_ok, log_step
from .materials import (
    EMPTY_ENTRY_KIND,
    MATERIALS_TOPIC,
    alias_kind,
    infer_kind,
    is_materials_repo,
)
from .materials import read as read_materials
from .opencourse import READINGS_MODES, OpenCourse
from .opencourse import seed_text as opencourse_text
from .profile_readme import profile_files, update_profile_readme
from .readings import is_reading_overlay
from .repos import (
    current_description,
    default_branch,
    rename_repo,
    repo_missing,
    set_repo_topics,
)
from .scaffold import materials_system_files, starter_line
from .setting_readers import read_settings
from .settings import RUN_KEYS
from .status_json import SET_ASIDE_KEY
from .team_formation import TEAM_LOCK_PATH, sync_team_lock, team_lock_content
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

# The old names this migration rewrites, every one of them out of `legacy`.
OLD_CONFIG_REPO = legacy.old_name(CONFIG_REPO, legacy.SEMESTER_ORG)
OLD_JOIN_REPO = legacy.old_name(JOIN_REPO, legacy.SEMESTER_ORG)
OLD_PEOPLE_FILE = legacy.old_name(INSTRUCTORS_FILE, legacy.SEMESTER_ORG)
OLD_SEMESTER_TOPIC = legacy.old_name(SEMESTER_TOPIC, legacy.SEMESTER_TOPICS)
OLD_SEMESTERS_PATH = legacy.old_name(SEMESTERS_PATH, legacy.COURSE_GITHUB)
OLD_TEMPLATE_PREFIX = legacy.old_name(TEMPLATE_TOPIC, legacy.COURSE_REPOS)
RETIRED_COURSE_KEYS = tuple(legacy.renames(legacy.COURSE_CONFIG))
# A 0.10 `dsl-course.yml`'s course-wide defaults block.
ASSIGNMENT_DEFAULTS_KEY = legacy.old_name("defaults", legacy.COURSE_FILE_KEYS)
LEGACY_SUBMIT_VIA = legacy.renames(legacy.GRADING_CONFIG)
RENAMED_SETTINGS = legacy.renames(legacy.SETTINGS)


def is_untopicked_template(row: dict) -> bool:
    """An assignment template by its old mark only - an `assignment-*` GitHub template
    without the topic, which the topic step stamps."""
    return (
        row["name"].startswith(OLD_TEMPLATE_PREFIX)
        and bool(row.get("isTemplate"))
        and not is_assignment_template(row)
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
    "enrolment/": f"{records.path('enrolment')}/",
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
# ... and where 0.10 put it in semester-config, before decision 0039 renamed it.
OLD_SEMESTER_POINTER = legacy.old_name(records.path("pointer"), legacy.SEMESTER_CONFIG)
# The 0.10 seeds the 0.10 steps compare against and write (decision 0039 replaced them):
# kept beside this module, and removed with it.
LEGACY_TEMPLATES = "legacy-0.10"


def old_template(rel: str) -> str:
    """A 0.10 seed, by its path under `templates/`."""
    return template(f"{LEGACY_TEMPLATES}/{rel}")


def old_scaffold(org: str, path: str, ref: str) -> str:
    """A 0.10 semester-config skeleton (`instructors.yml`, `assignments.yml`) exactly as
    Bootstrap semester seeded it for `org`, the institution's defaults filled in."""
    tag = semester_of(org) or "f2026"
    year = int(tag[1:])
    ours = settings.institution_defaults()
    return pin_central_ref(old_template(f"semester-config/{path}"), ref).format(
        tag=tag,
        year=year,
        year_next=year + 1,
        **{key: ours[key] for key in settings.RUN_KEYS if key in ours},
    )


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
STARTER_COMMIT = "migrate: starter"


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


def submit_via_line(line: str, indent: str = "") -> str:
    """One `submit_via: github` line (at `indent`) in the canonical word
    (`LEGACY_SUBMIT_VIA`), its comment kept at its column; any other line
    unchanged."""
    match = re.match(rf"^{indent}submit_via:(.*)$", line)
    if not match:
        return line
    value, tail = split_comment(match.group(1))
    word = LEGACY_SUBMIT_VIA.get(value.strip().strip("'\"").lower())
    if word is None:
        return line
    head = f"{indent}submit_via: {word}"
    if not tail:
        return head
    gap = len(line) - len(tail.lstrip()) - len(head)  # the comment's column kept
    if gap < 1:
        gap = len(tail) - len(tail.lstrip())  # ... or, past it, its spacing
    return head + " " * gap + tail.lstrip()


def grading_config_keys(text: str) -> str:
    """A template's `grading_config.yml`: the top-level `format:` becomes `formats:`, and
    `submit_via: github` reads `assignment_repo`."""
    return "\n".join(submit_via_line(formats_line(line)) for line in text.split("\n"))


def course_config_keys(text: str) -> str:
    """The course `dsl-course.yml`: every retired key (`RETIRED_COURSE_KEYS` -
    `org`, `org_name`, `cohort_defaults`, `semester_defaults`) removed with its block, and
    under `assignment_defaults:` `format:` -> `formats:` and `submit_via: github` ->
    `assignment_repo`. `assignment_defaults` stays: it is the course layer of the cascade
    (decision 0009). A blank line inside a removed block goes with it; the blank lines
    after it leave one, where the block was."""
    out, section = [], ""
    held: list[str] = []  # blank lines met inside a block being removed
    for line in text.split("\n"):
        if top := _TOP_KEY.match(line):
            section = top.group(1)
        if section in RETIRED_COURSE_KEYS and (top or line[:1] in (" ", "\t")):
            held = []
            continue  # the key's own line, or a line of its block
        if section in RETIRED_COURSE_KEYS and not line.strip():
            held.append(line)
            continue
        if section in RETIRED_COURSE_KEYS:
            section = ""  # a top-level comment ends the block
        if held and out and out[-1].strip():
            out.append("")
        held = []
        if section == ASSIGNMENT_DEFAULTS_KEY and not top:
            indent = re.match(r"^(\s+)(?:format|submit_via):", line)
            if indent:
                line = submit_via_line(
                    formats_line(line, indent.group(1)), indent.group(1)
                )
        out.append(line)
    if held and out and out[-1].strip():
        out.append("")
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
                defaults["archive_grace_days"],
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
    `semester_dest_repo` by then.

    A blank line or a comment inside a cut block does not end it: it is held until the
    next line says whether the block goes on (dropped with it) or has ended (kept, as
    what separates it from the next key). Ending the cut there kept the block's later
    lines, which then nested under whatever key came before."""
    out, section = [], ""
    scalar: int | None = None  # the column of the key whose block scalar this is in
    cut: int | None = None  # the column of the key being removed, with its value
    held: list[str] = []  # blank and comment lines met inside the cut block
    for line in text.split("\n"):
        if cut is not None:
            if line.strip() and not line.lstrip().startswith("#"):
                if _indent(line) > cut:
                    held = []
                    continue
            elif not line.strip() or _indent(line) <= cut:
                held.append(line)
                continue
            else:
                continue  # a comment indented into the block is part of it
            out += held
            held, cut = [], None
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
    return "\n".join(out + held)


def schedule_entries(text: str | None) -> dict | None:
    """`{top-level key: its entry keys}` of a `schedule.yml` (None for a key whose value
    is not a mapping), the retired top-level blocks left out: what the keys step must
    leave exactly as it found it. None when the text does not parse to a mapping."""
    try:
        data = yaml.safe_load(text or "") or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        str(key): sorted(map(str, value)) if isinstance(value, dict) else None
        for key, value in data.items()
        if key not in RETIRED_TOP
    }


def entries_kept(before: str | None, after: str | None) -> bool:
    """`after` parses, and holds the same blocks and entries as `before` (named when
    not, for a person to fix by hand)."""
    old, new = schedule_entries(before), schedule_entries(after)
    if new is None:
        log_err(f"{schedule.SCHEDULE_PATH} would not be valid YAML - fix by hand")
        return False
    if old is not None and old != new:
        changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        log_err(
            f"{schedule.SCHEDULE_PATH}: the rewrite would change the entries of "
            f"{', '.join(changed)} - fix by hand"
        )
        return False
    return True


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


# The one run setting the old engine read from the course's `assignment_defaults` (its
# team cap); every other one came from the template, else the toolkit's default.
OLD_COURSE_RUN_KEYS = ("max_team_size",)


def instance_additions(
    meta: dict,
    templates: dict[str, dict],
    lower: list,
    existing: settings.Instance,
) -> tuple[dict[str, dict], list[str]]:
    """What the semester's `assignments.yml` must say so this engine runs each assignment
    of `meta` (the OLD `schedule.yml`, parsed) as the old one did: `{key: {setting:
    value}}`, and a note for each late cutoff that moves by more than an hour.

    - each run setting as the old engine ran it - the template's (`templates`, `{template
      repo: raw run keys}`), else the institution's default (the course's only for
      `OLD_COURSE_RUN_KEYS`) - wherever the layers below (`lower`: the semester's
      defaults, the course, the institution) would give another;
    - `late_window_days` (with the penalty beside it) where `grading_datetime` was not the
      due date plus the window: the cutoff is computed now, rounded to whole days;
    - `semester_dest_repo`, moved out of the schedule.
    A setting the file already states is left as written."""
    tz = schedule._tz(meta.get("timezone"))
    named = dict(lower)
    course = named.get("course", {})
    toolkit = {
        **named.get("institution", {}),
        **{k: course[k] for k in OLD_COURSE_RUN_KEYS if course.get(k) is not None},
    }
    out: dict[str, dict] = {}
    notes: list[str] = []
    entries = meta.get("assignments")
    for slug, entry in entries.items() if isinstance(entries, dict) else ():
        if not isinstance(entry, dict):
            continue
        slug = str(slug)
        own = _read_run_keys(templates.get(str(entry.get("course_source_repo")), {}))
        old = [("template", own), ("toolkit", toolkit)]
        want: dict = {}
        for key in settings.RUN_KEYS:
            if key in settings.LATE_PAIR:
                continue
            value, _ = settings.resolve(key, old)
            if value is not None and value != settings.resolve(key, lower)[0]:
                want[key] = value
        pair = {k: settings.resolve(k, old)[0] for k in settings.LATE_PAIR}
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
            "# semester_start:                   # OPTIONAL - NO default. The site's "
            '"Semester starts" row'
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
            "Schedule tab. The notice email 14 days out goes either way"
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
        # The old pointer at the retired `publish.yml` (decision 0016), in both of the
        # spellings a live course carries: it names `opencourse.yml` now.
        **dict.fromkeys(
            (
                f"#   # WHICH of those files the {word} site hosts publicly, so an HTML "
                "deck opens rendered"
                for word in ("cohort", "semester")
            ),
            "#   # WHICH files the public website shows, and how, is `opencourse.yml` "
            "beside this",
        ),
        "#   # instead of showing as source, is `publish.yml` in the materials repo - "
        "not here.": "#   # file - not here.",
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
    new = old_template("course/people-header.yml")
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


# ------------------------------------------------------------------ public website
# Decision 0016: the public website's settings move from the `_publish-config.yml` a
# publish left in the site repo into the course's `.github/opencourse.yml`. The step seeds
# `opencourse.yml` once; the next publish deletes the old settings file. A materials repo's
# `publish.yml` is left alone: it is the semester site's hosted-copy opt-in again
# (decision 0035 rule 1).
RETIRED_PUBLISH_CONFIG = "_publish-config.yml"
PUBLISH_COMMIT = "migrate: seed opencourse.yml"


def opencourse_from(config: str | None) -> OpenCourse:
    """`opencourse.yml`'s values from a leftover `_publish-config.yml`: on, with its
    settings, when it names a source repo; else off."""
    try:
        cfg = yaml.safe_load(config or "")
    except yaml.YAMLError:
        cfg = None
    if not isinstance(cfg, dict) or not cfg.get("source_repo"):
        return OpenCourse()
    mode = str(cfg.get("readings_mode") or "")
    return OpenCourse(
        enabled=True,
        source_repo=str(cfg["source_repo"]),
        readings_mode=mode if mode in READINGS_MODES else READINGS_MODES[0],
        include_lectures=bool(cfg.get("include_lectures", True)),
    )


# ------------------------------------------------------------------ schedule shape
# Decision 0038: schedule.yml goes from one `releases:` block (kind and number on the entry
# or inferred from a folder or a label) and `events:` (display rows and a lab's second
# group) to one block per kind. Each entry keeps its KEY (it already names its row and
# ids), its kind is written by the block it lands in (today's inference, once), lectures
# and labs get `session:` (their number today), a readings entry the site joins to a
# lecture gets that lecture's `session:`, `number:` only where it differs, a lab's second
# group becomes a slot of its lab, the copies' fields are renamed, a READINGS.md becomes
# the entry's `reading_list:`, and each assignment's `assignments.yml` block moves onto
# its entry. Rendered rows stay as they were, except what 0038 drops.
SHAPE_COMMIT = "migrate: schedule shape"
SHAPE_HEADER = """\
# INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.
#
# This semester's plan: one block per kind (lectures, labs, readings, exams, events, assets),
# each a map whose KEY names what the toolkit makes for the entry (its site row, its ids; an
# assignment's repos and mark sheet) and is never displayed or read for a number.
#   session         the teaching unit (integer): REQUIRED on lectures and labs; optional on
#                   readings (absent = a standalone row)
#   number          optional, default = session: the row word ("Lab 2"); REQUIRED on assignments
#   event_datetime  a datetime, `tbc`, or a list of slots {at, location, details}
#   files           from_course_repo, from_path, to_semester_repo, to_path, release_datetime
# The term's own dates are the `semester:` block, each entry with a `type`: start, end,
# break (`from`/`to`) or archive. Field reference: docs/07-schedule-releases.md.
"""
# A label's own number, as 0.10 read it: trailing (`lecture_03`, `lab-9`) or leading
# (`01_lab`). The migration reads it ONCE, to write `session:` / `number:`.
_LABEL_NUMBER = re.compile(r"0*(\d+)$|^0*(\d+)[-_ ]")
_FILE_FIELDS = legacy.renames(legacy.SCHEDULE_FILE)
_ASSIGNMENT_FIELDS = legacy.renames(legacy.SCHEDULE_ASSIGNMENT)
# The 0.10 top-level keys of the term's own dates, and the `semester:` entry each becomes
# (decision 0038 amendment 2): key and `type`.
_SEMESTER_KEYS = {
    "semester_start": "start",
    "semester_end": "end",
    "archive": "archive",
}


def _semester_block(old: dict) -> dict:
    """The `semester:` block for a file that may still carry `semester_start`,
    `semester_end` and `archive:` at the top: each becomes an entry of its `type`, beside
    whatever entries the block already has."""
    block = dict(old.get(schedule.SEMESTER_BLOCK) or {})
    for top, kind in _SEMESTER_KEYS.items():
        if top not in old or kind in block:
            continue
        value = old[top]
        if kind == "archive":
            extra = value if isinstance(value, dict) else {}
            block[kind] = {"type": kind, **extra}
        elif value not in (None, ""):
            block[kind] = {"type": kind, "event_datetime": value}
    return block


def _guide(entry: dict) -> dict:
    """A readings entry with `reading_list:` renamed `reading_guide:` (amendment 2)."""
    if "reading_list" not in entry:
        return entry
    return {
        ("reading_guide" if k == "reading_list" else k): v for k, v in entry.items()
    }


class _Verbatim(yaml.SafeLoader):
    """A SafeLoader that leaves dates and datetimes as the text they were written as, so
    the rewritten file says `2026-09-01T18:00` where the old one did."""


_Verbatim.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in rules if tag != "tag:yaml.org,2002:timestamp"]
    for first, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _label_number(label: str) -> int | None:
    m = _LABEL_NUMBER.search(label.strip())
    return int(m.group(1) or m.group(2)) if m else None


def _own_number(entry: dict, key: str) -> int | None:
    """An entry's number as 0.10 read it: its `number:`, else its key's."""
    n = entry.get("number")
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        return n
    return _label_number(key)


def _old_kind(entry: dict, aliases: Aliases) -> str:
    """The kind 0.10 gave a `releases:` entry: its `kind:`, else the section its first copy
    lands in (the source repo's `materials.yml` aliases, else the built-in ones), else
    `lecture` for an entry that copies nothing."""
    kind = str(entry.get("kind") or "").strip().lower()
    if kind:
        return kind if kind in policy.content_kinds() else policy.FALLBACK_KIND
    deploys = entry.get("deploy")
    deploys = [deploys] if isinstance(deploys, dict) else deploys or []
    deploys = [d for d in deploys if isinstance(d, dict)]
    if not deploys:
        return EMPTY_ENTRY_KIND
    return _copy_kind(deploys[0], aliases)


def _copy_kind(copy: dict, aliases: Aliases) -> str:
    """The kind 0.10 read off where one `deploy:` copy lands: the section its destination
    names (the source repo's `materials.yml` aliases, else the built-in ones)."""
    kinds = aliases(str(copy.get("course_source_repo") or ""))
    dest = str(copy.get("semester_dest_path") or copy.get("course_source_path") or "")
    head, sep, _ = dest.strip("/").partition("/")
    section = (
        head
        if sep or (head and alias_kind(head, kinds))
        else str(copy.get("semester_dest_repo") or schedule.DEFAULT_DEST_REPO)
    )
    return infer_kind(section, kinds)


def _copies(entry: dict) -> list[dict]:
    """The `deploy:` copies as `files:` items: each field renamed (`legacy`)."""
    deploys = entry.get("deploy")
    deploys = [deploys] if isinstance(deploys, dict) else deploys or []
    return [
        {_FILE_FIELDS.get(k, k): v for k, v in d.items() if v not in (None, "")}
        for d in deploys
        if isinstance(d, dict)
    ]


def _readings_twin(
    key: str,
    kind: str,
    entry: dict,
    new: dict,
    aliases: Aliases,
    reading_list: ReadingList,
) -> tuple[str, dict] | None:
    """A lecture's or lab's copies that land in a readings section, moved off `new` into a
    readings entry of the same session (`<key>-readings`): under 0.11 the block says what
    a file is, so a session's own row lists only its own files and its readings attach to
    the unit as any readings entry does. Same date, so the copies ship when they did. None
    when it copied no readings."""
    if kind not in schedule.SESSION_KINDS or not new.get("session"):
        return None
    deploys = entry.get("deploy")
    deploys = [deploys] if isinstance(deploys, dict) else deploys or []
    deploys = [d for d in deploys if isinstance(d, dict)]
    split = [i for i, d in enumerate(deploys) if _copy_kind(d, aliases) == "readings"]
    if not split:
        return None
    files = new["files"]
    moved = [files[i] for i in split]
    new["files"] = [f for i, f in enumerate(files) if i not in split]
    twin = {"session": new["session"], "event_datetime": new.get("event_datetime")}
    if "tbc" in new:
        twin["tbc"] = new["tbc"]
    twin["files"] = moved
    texts = [
        t.strip()
        for f in moved
        if (t := reading_list(str(f.get("from_course_repo")), str(f.get("from_path"))))
        and t.strip()
    ]
    if texts:
        twin["reading_guide"] = "\n\n".join(texts) + "\n"
    return f"{key}-readings", {k: v for k, v in twin.items() if v not in (None, "")}


def _first_time(value: object) -> str | None:
    """A written date or datetime, None for `tbc` or nothing."""
    text = str(value or "").strip()
    return None if not text or text.lower() == "tbc" else text


def _hour_before(text: str, tz) -> str | None:
    """When a copy with no time of its own shipped under 0.10: an hour before a timed
    event, the day's start for a whole-day one."""
    when = schedule._coerce_date_or_datetime(text, tz)
    if isinstance(when, datetime):
        return f"{when - schedule.DEFAULT_DEPLOY_LEAD:%Y-%m-%dT%H:%M}"
    return f"{when:%Y-%m-%d}" if when else None


# A source repo's `materials.yml` folder aliases (None: it does not parse).
Aliases = Callable[[str], Mapping[str, str] | None]
ReadingList = Callable[[str, str], str]


def schedule_shape(
    text: str,
    instance_text: str | None = None,
    aliases: Aliases = lambda repo: {},
    reading_list: ReadingList = lambda repo, path: "",
) -> tuple[str, str | None, list[str], list[str]]:
    """`(schedule.yml, assignments.yml or None when it has no `assignments:` block,
    notes, stops)` for a 0.10 `text` (and the `assignments.yml` beside it): the 0.11 shape
    (decision 0038), rows unchanged but for what 0038 drops. `aliases(repo)` are a source
    repo's `materials.yml` folder aliases (today's kind inference); `reading_list(repo,
    path)` the READINGS.md text a copy of `path` from `repo` carries ("" for none).
    `stops` are what a person must settle first; `notes` what changes for them to check."""
    notes: list[str] = []
    stops: list[str] = []
    old = yaml.load(schedule_timing(schedule_keys(text)), _Verbatim) or {}
    if not isinstance(old, dict):
        return text, instance_text, notes, ["schedule.yml is not a mapping"]
    if "releases" not in old and any(
        isinstance(old.get(b), dict) for b in schedule.BLOCKS if b not in ("events",)
    ):
        # Already one block per kind (a file shaped before amendment 2): only the term's
        # dates and the readings guide take their new names.
        shaped = {
            b: {k: _guide(e) if isinstance(e, dict) else e for k, e in old[b].items()}
            for b in schedule.BLOCKS
            if isinstance(old.get(b), dict)
        }
        assignments = (
            old.get("assignments") if isinstance(old.get("assignments"), dict) else {}
        )
        return _shape_text(text, old, shaped, assignments, notes), None, notes, stops
    tz = schedule._tz(old.get("timezone"))
    releases = old.get("releases") if isinstance(old.get("releases"), dict) else {}
    events = old.get("events") if isinstance(old.get("events"), dict) else {}
    blocks: dict[str, dict[str, dict]] = {b: {} for b in schedule.BLOCKS}
    plain = lambda e: {k: v for k, v in e.items() if v not in (None, "")}

    # 1. Releases, by today's kind. Lectures' numbers are what readings join.
    kinds = {
        str(k): _old_kind(e, aliases)
        for k, e in releases.items()
        if isinstance(e, dict)
    }
    numbers = {k: _own_number(releases[k], k) for k in kinds}
    lectures = {
        numbers[k]
        for k in kinds
        if kinds[k] == "lecture"
        and numbers[k]
        and releases[k].get("show_on_site") is not False
    }
    hosts: dict[
        tuple[str, int], str
    ] = {}  # (kind, n) -> the shown key a session repeats
    # (key, entry) of each readings entry split off a lecture's or lab's copies.
    twins: list[tuple[str, dict]] = []
    for key, kind in kinds.items():
        entry, n = releases[key], numbers[key]
        new: dict = {}
        if kind in schedule.SESSION_KINDS:
            if n:
                new["session"] = n
            else:
                notes.append(f"{key}: no number today, so no session - give it one")
        elif kind == "readings":
            if n and n in lectures:
                new["session"] = n
            elif n and entry.get("show_on_site") is not False:
                new["number"] = n
        elif n and kind in policy.numbered_kinds():
            new["number"] = n
        new |= {
            k: entry[k]
            for k in (
                "title",
                "details",
                "event_datetime",
                "location",
                "show_on_site",
                "tbc",
            )
            if k in entry and entry[k] not in (None, "")
        }
        if "deploy" in entry or kind in schedule.DISPLAY_KINDS:
            new["files"] = _copies(entry)
        twin = _readings_twin(key, kind, entry, new, aliases, reading_list)
        if twin:
            twins.append(twin)
        if kind == "readings":
            texts = [
                t.strip()
                for f in new.get("files", [])
                if (
                    t := reading_list(
                        str(f.get("from_course_repo")), str(f.get("from_path"))
                    )
                )
                and t.strip()
            ]
            if texts:
                new["reading_guide"] = "\n\n".join(texts) + "\n"
        blocks[policy.plural_of(kind)][key] = new
        if n and entry.get("show_on_site") is not False:
            hosts.setdefault((kind, n), key)

    for key, twin in twins:
        if key in kinds or key in blocks["readings"]:
            stops.append(
                f"{key}: the readings split off its session need this key - free it"
            )
            continue
        blocks["readings"][key] = twin
        notes.append(
            f"{key}: the readings its session copied, now a readings entry of that session"
        )

    # 2. Events: exams and special events are display rows; a session of a content kind
    # repeats a release - a slot of it where it says nothing of its own but its date,
    # room and words, else an entry of its own in the same unit.
    for key, entry in events.items():
        if not isinstance(entry, dict):
            continue
        key = str(key)
        kind = str(entry.get("kind") or "special_event").strip().lower()
        if kind in ("exam", "special_event"):
            block = (
                "exams" if kind == "exam" else policy.plural_of(policy.FALLBACK_KIND)
            )
            blocks[block][key] = plain(
                {
                    k: entry.get(k)
                    for k in (
                        "title",
                        "details",
                        "event_datetime",
                        "location",
                        "show_on_site",
                        "tbc",
                    )
                }
            )
            continue
        if kind not in policy.content_kinds():
            kind = policy.FALLBACK_KIND
        m = re.search(r"\d+", key)
        n = (
            _own_number(entry, key)
            if entry.get("number")
            else (int(m.group()) if m else None)
        )
        host = hosts.get((kind, n)) if n else None
        target = blocks[policy.plural_of(kind)]
        own_title = str(entry.get("title") or "")
        if (
            host
            and entry.get("show_on_site") is not False
            and not entry.get("tbc")
            and own_title in ("", str(target[host].get("title") or ""))
            and _first_time(entry.get("event_datetime"))
            and _first_time(target[host].get("event_datetime"))
            and not target[host].get("tbc")
        ):
            first = target[host]
            slots = first["event_datetime"]
            if not isinstance(slots, list):
                slots = [slots]
            slot = plain(
                {
                    "at": entry["event_datetime"],
                    "location": entry.get("location"),
                    "details": entry.get("details"),
                }
            )
            slots.append(slot if len(slot) > 1 else slot["at"])
            # Files still ship before the release's own date, not the earlier group's.
            at = _hour_before(str(slots[0]), tz)
            for f in first.get("files", []):
                f.setdefault("release_datetime", at)
            first["event_datetime"] = slots
            continue
        new = {"session": n} if n and kind in schedule.SESSION_KINDS else {}
        if n and kind not in schedule.SESSION_KINDS:
            new["number"] = n
        new |= plain(
            {
                k: entry.get(k)
                for k in (
                    "details",
                    "event_datetime",
                    "location",
                    "show_on_site",
                    "tbc",
                )
            }
        )
        title = own_title or (str(target[host].get("title") or "") if host else "")
        if title:
            new["title"] = title
        if key in target:
            stops.append(f"events.{key}: its key is a {kind} entry's too - rename one")
            continue
        target[key] = new
    taken: dict[str, str] = {}
    for block, entries in blocks.items():
        for key in entries:
            if key in taken:
                stops.append(
                    f"{block}.{key}: the key of a {taken[key]} entry too - rename one"
                )
            taken[key] = block

    # 3. Assignments: `template`, `number`, `repo_name` where it is not the default, and
    # the `assignments.yml` block inline.
    instance = settings.parse_legacy_assignments(instance_text)
    raw_blocks = (
        (yaml.load(instance_text or "", _Verbatim) or {}) if instance_text else {}
    )
    raw_blocks = raw_blocks.get("assignments") if isinstance(raw_blocks, dict) else None
    raw_blocks = raw_blocks if isinstance(raw_blocks, dict) else {}
    assignments: dict[str, dict] = {}
    for key, entry in (old.get("assignments") or {}).items():
        if not isinstance(entry, dict):
            continue
        key = str(key)
        n = _own_number(entry, key)
        new = {}
        if n:
            new["number"] = n
        else:
            notes.append(f"assignments.{key}: no number today - give it one")
        if entry.get("course_source_repo") is not None:
            new["template"] = entry["course_source_repo"]
        name = instance.dests.get(key) or key
        if name != key:
            new["repo_name"] = name
        new |= plain(
            {
                _ASSIGNMENT_FIELDS.get(k, k): v
                for k, v in entry.items()
                if k not in ("number", "course_source_repo")
            }
        )
        block = raw_blocks.get(key) if isinstance(raw_blocks.get(key), dict) else {}
        new |= {
            settings.INLINE_NAMES.get(k, k): v
            for k, v in block.items()
            if k in settings.RUN_KEYS and v is not None
        }
        assignments[key] = new
    for key in sorted(set(map(str, raw_blocks)) - set(assignments)):
        stops.append(
            f"{ASSIGNMENTS_FILE} assignments.{key}: no such assignment in schedule.yml"
        )

    instance_after = _without_block(instance_text, settings.ASSIGNMENTS_BLOCKS)
    return (
        _shape_text(text, old, blocks, assignments, notes),
        instance_after,
        notes,
        stops,
    )


def _shape_text(
    text: str, old: dict, blocks: dict, assignments: dict, notes: list[str]
) -> str:
    """The rewritten schedule.yml: the header, `timezone`, the `semester:` block, one
    block per kind and `assignments:`; `notes` gains the count of comments left behind."""
    out = [SHAPE_HEADER]
    if "timezone" in old:
        out.append(_emit({"timezone": old["timezone"]}))
    semester = _semester_block(old)
    if semester:
        out.append(_emit({schedule.SEMESTER_BLOCK: semester}))
    for block, entries in blocks.items():
        if entries:
            out.append(_emit({block: entries}))
    if assignments:
        out.append(_emit({"assignments": assignments}))
    comments = sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))
    if comments:
        # Rewritten from the data: a comment has no entry to travel with. Counted, never
        # quoted (a person's words may name someone); git history keeps the old file.
        notes.append(
            f"{comments} comment line(s) of {schedule.SCHEDULE_PATH} are not carried "
            "into the new shape - the old file stays in its history"
        )
    return "\n".join(out)


_DATE_LIKE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?)?$"
)


def _scalar_text(value: object) -> str:
    """One scalar as YAML: a written date as written, anything else as YAML quotes it."""
    if isinstance(value, str) and _DATE_LIKE.match(value):
        return value
    return (
        yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10**6)
        .removesuffix("\n...\n")
        .strip()
    )


def _emit(value: object, indent: int = 0) -> str:
    """`value` (mappings, lists and scalars) as block YAML at `indent`; a string with a
    line break as a literal block."""
    pad = " " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            key = (
                _scalar_text(k)
                if not isinstance(k, str)
                else (k if re.fullmatch(r"[A-Za-z0-9_.-]+", k) else _scalar_text(k))
            )
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{key}:")
                lines.append(_emit(v, indent + 2))
            elif isinstance(v, str) and "\n" not in v.strip():
                lines.append(f"{pad}{key}: {_scalar_text(v.strip())}")
            elif isinstance(v, str):
                lines.append(f"{pad}{key}: |")
                lines += [
                    f"{pad}  {line}" if line else ""
                    for line in v.rstrip("\n").split("\n")
                ]
            elif isinstance(v, (dict, list)):
                lines.append(f"{pad}{key}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                lines.append(f"{pad}{key}: {_scalar_text(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict) and item:
                body = _emit(item, indent + 2).split("\n")
                lines.append(f"{pad}- {body[0].lstrip()}")
                lines += body[1:]
            else:
                lines.append(f"{pad}- {_scalar_text(item)}")
        return "\n".join(lines)
    return f"{pad}{_scalar_text(value)}"


def _without_block(text: str | None, block: str) -> str | None:
    """`text` without its top-level `block:` and everything under it (comments above the
    next top-level key kept); None when it has no such block."""
    if not text or not re.search(rf"^{block}:", text, re.MULTILINE):
        return None
    out, cutting = [], False
    for line in text.split("\n"):
        if re.match(rf"^{block}:", line):
            cutting = True
            continue
        if cutting and line and not line[0].isspace() and not line.startswith("#"):
            cutting = False
        if not cutting:
            out.append(line)
    return re.sub(r"\n\n\n+", "\n\n", "\n".join(out)).rstrip("\n") + "\n"


# ------------------------------------------------------------------ config layers
# Decision 0039: one hand-edited file per level. The course's `dsl-course.yml` and
# `opencourse.yml` fold into `.github/course.yml`; the semester's `instructors.yml`,
# `assignments.yml` `defaults:` and its source repos' `materials.yml` `syllabus:` fold into
# `semester-config/semester.yml`; `students.csv` + `teams.csv` split into `students.csv`
# (team columns) and `.system/roster.csv`; the semester's pointer moves to
# `.system/course.yml`. Each new file is checked against the old ones before anything is
# written (`*_kept`): a value the fold would lose stops the step.
LAYERS_COMMIT = "migrate: config layers"
# The old course file's keys and where each lands (`legacy.COURSE_FILE_KEYS`; "" = dropped).
COURSE_KEY_MOVES = legacy.renames(legacy.COURSE_FILE_KEYS)
# An old defaults block's keys that changed (`legacy.DEFAULTS_KEYS`; "" = dropped).
DEFAULTS_KEY_MOVES = legacy.renames(legacy.DEFAULTS_KEYS)
# Kept as they were, at the top of course.yml: the set-aside to-dos (decision 0032).
COURSE_KEPT_KEYS = (SET_ASIDE_KEY,)
# The course block's keys, in the order course.yml writes them.
_COURSE_ORDER = (
    "name",
    "code",
    "description",
    TOOLKIT_REF_KEY,
    "licence",
    "student_console",
)
# What the old roster files were, by their rows: the old names `roster.split_legacy` reads.
OLD_TEAMS_PATH = legacy.old_name(
    "students.csv `team:<number>` columns", legacy.ROSTER_FILES
)
OLD_SEMESTER_FILES = (INSTRUCTORS_FILE, ASSIGNMENTS_FILE, OLD_TEAMS_PATH)


def _old_defaults(raw: object) -> tuple[dict, list[str]]:
    """An old `assignment_defaults:` block with its keys renamed (`visibility` ->
    `assignment_repo_visibility`), and the keys dropped (`formats`, which the New
    assignment wizard asks)."""
    if not isinstance(raw, dict):
        return {}, []
    out, dropped = {}, []
    for key, value in raw.items():
        new = DEFAULTS_KEY_MOVES.get(str(key), str(key))
        if not new:
            dropped.append(str(key))
            continue
        out[new] = value
    return out, dropped


def _stated(block: dict, where: str) -> tuple[dict, list[str]]:
    """`block` (a `defaults:` block as the files spell it) through the readers, keyed by
    setting, and a line for each value a reader refuses: the fold never drops one
    silently."""
    dropped: list = []
    values = settings.read_defaults(
        block, where, dropped, (*settings.DEFAULT_KEYS, *settings.COURSE_ASKED_KEYS)
    )
    return (
        {k: v for k, v in values.items() if v is not None},
        [getattr(d, "what", str(d)) for d in dropped],
    )


def _defaults_text(inherited: list, stated: dict) -> str:
    """A `defaults:` skeleton with `stated` live; a stated `submit_via` (not in the fixed
    key set, kept readable at the course level) after it."""
    text = settings.defaults_skeleton(inherited, stated)
    for key in settings.COURSE_ASKED_KEYS:
        if key in stated:
            text += f"  {key}: {_scalar_text(stated[key])}\n"
    return text


def old_opencourse_text(oc: OpenCourse) -> str:
    """`opencourse.yml` as the 0.10 public-website step seeded it: the `open_course_site:`
    block's lines at the top level."""
    body = opencourse_text(oc).split("\n", 1)[1]
    return (
        "# INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.\n"
        + textwrap.dedent(body)
    )


def course_layer(
    meta_text: str | None, oc_text: str | None
) -> tuple[str, list[str], list[str]]:
    """`(course.yml, notes, stops)` folded out of a 0.10 `dsl-course.yml` and
    `opencourse.yml`. `notes` name what decision 0039 drops; `stops` name what has no home
    in the new file (a person moves it, then the step runs). Pure."""
    meta = _yaml(meta_text)
    notes: list[str] = []
    stops = [
        f"{COURSE_CONFIG}: `{key}:` has no place in {NEW_COURSE_FILE} - move or delete "
        f"it by hand, then run again"
        for key in meta
        if key not in COURSE_KEY_MOVES and key not in COURSE_KEPT_KEYS
    ]
    identity: dict = {}
    for old, new in COURSE_KEY_MOVES.items():
        if new.startswith(f"{COURSE_BLOCK}.") and meta.get(old) not in (None, ""):
            identity[new.split(".", 1)[1]] = meta[old]
        elif not new and old in meta:
            notes.append(f"dropped (decision 0039): `{old}:`")
    raw_people = meta.get(PEOPLE_BLOCK)
    people = raw_people if isinstance(raw_people, dict) else {}
    for role in ("instructors", "teaching_assistants"):
        if people.get(role):
            notes.append(
                f"dropped (decision 0039 rule 3): course-level `people.{role}` "
                f"({len(people[role])} card(s)); a semester's semester.yml names its own"
            )
    defaults, gone = _old_defaults(meta.get(ASSIGNMENT_DEFAULTS_KEY))
    notes += [
        f"dropped (decision 0039 rule 2): `{ASSIGNMENT_DEFAULTS_KEY}.{key}` (the New "
        f"assignment wizard asks)"
        for key in gone
    ]
    stated, refused = _stated(defaults, f"{COURSE_CONFIG} {ASSIGNMENT_DEFAULTS_KEY}")
    stops += [f"{COURSE_CONFIG}: {line} - fix it first" for line in refused]
    try:
        oc = opencourse.parse(_yaml(oc_text), OPENCOURSE_FILE) if oc_text else None
    except Unusable as exc:
        oc, stops = None, [*stops, f"{OPENCOURSE_FILE}: {exc} - fix it first"]
    header = template("course/course.yml").split(f"{COURSE_BLOCK}:\n", 1)[0]
    lines = [header + f"{COURSE_BLOCK}:"]
    lines += [
        f"  {key}: {_scalar_text(identity[key])}"
        for key in _COURSE_ORDER
        if key in identity
    ]
    admins = course_admins(meta)
    out = "\n".join(lines) + "\n\n"
    if admins:
        out += f"{PEOPLE_BLOCK}:\n  course_admins:\n" + _emit(admins, 4) + "\n\n"
    out += _defaults_text([("institution", settings.institution_defaults())], stated)
    out += "\n" + opencourse_text(oc or OpenCourse(), str(meta.get("contact") or ""))
    for key in COURSE_KEPT_KEYS:
        if meta.get(key):
            out += f"\n{key}:\n" + _emit(meta[key], 2) + "\n"
    notes += _comments_note(meta_text, COURSE_CONFIG)
    return out, notes, stops


def _comments_note(text: str | None, path: str) -> list[str]:
    """A note counting `text`'s comment lines, which a fold does not carry (never quoted:
    a person's words may name someone)."""
    count = sum(1 for ln in (text or "").split("\n") if ln.lstrip().startswith("#"))
    return (
        [f"{count} comment line(s) of {path} are not carried: they stay in its history"]
        if count
        else []
    )


def course_layer_kept(
    meta_text: str | None, oc_text: str | None, new: str
) -> list[str]:
    """What `new` (a course.yml) says differently from the old files, one line per value;
    `[]` when every value the fold keeps is kept."""
    old, now = _yaml(meta_text), _yaml(new)
    out = []
    block = course_block(now)
    for key, place in COURSE_KEY_MOVES.items():
        stated = place.startswith(f"{COURSE_BLOCK}.") and old.get(key) not in (None, "")
        if stated and block.get(place.split(".", 1)[1]) != old[key]:
            out.append(f"`{key}` -> `{place}`")
    if course_admins(now) != course_admins(old):
        out.append("`people.course_admins`")
    before = _stated(_old_defaults(old.get(ASSIGNMENT_DEFAULTS_KEY))[0], "")[0]
    if settings.parse_course_defaults(now.get(DEFAULTS_BLOCK)) != before:
        out.append(f"`{ASSIGNMENT_DEFAULTS_KEY}` -> `{DEFAULTS_BLOCK}`")
    site = (
        now.get(OPEN_SITE_BLOCK) if isinstance(now.get(OPEN_SITE_BLOCK), dict) else {}
    )
    try:
        was = opencourse.parse(_yaml(oc_text)) if oc_text else OpenCourse()
        if opencourse.parse(site) != was:
            out.append(f"{OPENCOURSE_FILE} -> `{OPEN_SITE_BLOCK}`")
    except Unusable:
        out.append(f"{OPENCOURSE_FILE} -> `{OPEN_SITE_BLOCK}` (does not validate)")
    if str(site.get("contact") or "") != str(old.get("contact") or ""):
        out.append(f"`contact` -> `{OPEN_SITE_BLOCK}.contact`")
    for key in COURSE_KEPT_KEYS:
        if now.get(key) != old.get(key):
            out.append(f"`{key}`")
    return out


def _top_block(text: str, key: str) -> list[str] | None:
    """The lines of `text`'s top-level `key:` block, its own line first, through its last
    indented line (comments and blank lines after it left out); None when it has none."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(rf"^{key}:", ln)), None)
    if start is None:
        return None
    end = start + 1
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        if line.strip() and not line.startswith("#"):
            end = i + 1
    return lines[start:end]


def semester_layer(
    org: str,
    ref: str,
    instructors: str | None,
    assignments: str | None,
    syllabus: str,
    inherited: list,
) -> tuple[str, list[str]]:
    """`(semester.yml, stops)` folded out of a 0.10 `instructors.yml` (its list becomes
    `people:`, faculty's lines and comments as written), `assignments.yml` `defaults:` (the
    skeleton's live keys; every other key commented with what it inherits from
    `inherited`) and the declared `syllabus` (`student_site.syllabus`; "" = none). Pure."""
    stops: list[str] = []
    meta = _yaml(instructors)
    extra = [k for k in meta if k != "instructors"]
    stops += [
        f"{INSTRUCTORS_FILE}: `{k}:` has no place in {SEMESTER_FILE} - move or delete it "
        f"by hand, then run again"
        for k in extra
    ]
    old = settings.parse_legacy_assignments(assignments)
    if old.blocks:
        stops.append(
            f"{ASSIGNMENTS_FILE}: per-assignment blocks are still there - the schedule "
            f"shape step moves them onto their entries first"
        )
    stops += [
        f"{ASSIGNMENTS_FILE} {f.label}: {f.what} - fix it first" for f in old.faults
    ]
    tag = semester_of(org) or "f2026"
    year = int(tag[1:])
    text = pin_central_ref(template(f"semester-config/{SEMESTER_FILE}"), ref).format(
        year=year,
        year_next=year + 1,
        defaults=settings.defaults_skeleton(inherited, old.defaults),
    )
    people = _top_block(instructors or "", "instructors") if instructors else None
    if people and isinstance(meta.get("instructors"), list) and meta["instructors"]:
        head, rest = text.split("# people:\n", 1)
        rest = rest.split("\n\n", 1)[1]
        text = head + "\n".join([f"{PEOPLE_BLOCK}:", *people[1:]]) + "\n\n" + rest
    if syllabus:
        head, rest = text.split(f"# {STUDENT_SITE_BLOCK}:\n", 1)
        rest = rest.split("\n", 1)[1] if "\n" in rest else ""
        text = (
            head
            + f"{STUDENT_SITE_BLOCK}:\n  syllabus: {_scalar_text(syllabus)}"
            + "   # the released path the home page pins\n"
            + rest
        )
    return text, stops


def semester_layer_kept(
    instructors: str | None, assignments: str | None, syllabus: str, new: str
) -> list[str]:
    """What `new` (a semester.yml) says differently from the old files; `[]` when every
    value is kept."""
    out = []
    now = _yaml(new)
    if (now.get(PEOPLE_BLOCK) or []) != (_yaml(instructors).get("instructors") or []):
        out.append(f"{INSTRUCTORS_FILE} -> `{PEOPLE_BLOCK}`")
    if (
        settings.parse_instance(new).defaults
        != settings.parse_legacy_assignments(assignments).defaults
    ):
        out.append(f"{ASSIGNMENTS_FILE} `defaults:` -> `{DEFAULTS_BLOCK}`")
    site = now.get(STUDENT_SITE_BLOCK) or {}
    if str(site.get("syllabus") or "") != syllabus:
        out.append(f"`syllabus` -> `{STUDENT_SITE_BLOCK}.syllabus`")
    return out


def roster_kept(
    students: str, teams: str | None, new_students: str, new_system: str, cols: dict
) -> list[str]:
    """Counts of what the split would lose (never a value: the roster is per-person):
    `[]` when every row's email, name, role, handle, id, code and send time, and every
    team row, is kept."""
    old = list(csv.DictReader(io.StringIO(students.lstrip("﻿"))))
    email = roster.LEGACY_EMAIL
    by_email = {
        (r.get("email") or "").strip().casefold(): r
        for r in csv.DictReader(io.StringIO(new_students))
    }
    system = {
        (r.get("email") or "").strip().casefold(): r
        for r in csv.DictReader(io.StringIO(new_system))
    }
    lost = 0
    for row in old:
        key = (row.get(email) or row.get("email") or "").strip().casefold()
        now = {**by_email.get(key, {}), **system.get(key, {})}
        for column, value in row.items():
            name = "email" if column == email else column
            if (value or "").strip() and (now.get(name) or "").strip() != value.strip():
                lost += 1
    handle_email = {
        (r.get("github_handle") or "").strip().casefold(): (r.get(email) or "")
        .strip()
        .casefold()
        for r in old
    }
    folded = {k.casefold(): v for k, v in cols.items()}
    for t in csv.DictReader(io.StringIO((teams or "").lstrip("﻿"))):
        key = (t.get("assignment") or "").strip().casefold()
        who = handle_email.get((t.get("github_handle") or "").strip().casefold())
        if not who:
            continue  # on no roster row: never a team member (split_legacy drops it)
        column = roster.team_column(folded.get(key, key))
        if (by_email.get(who, {}).get(column) or "").strip().casefold() != (
            t.get("team") or ""
        ).strip().casefold():
            lost += 1
    return [f"{lost} roster value(s) would not survive the split"] if lost else []


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
    forget_all()  # and any read-once memo, should this CLI ever opt in to one


def _topics(listing: dict[str, dict]) -> set[str]:
    return set((listing.get(".github") or {}).get("topics") or [])


def _files(org: str, repo: str, branch: str = "") -> dict[str, str]:
    """`{path: blob sha}` of `repo`, or `{}` when the repo is not there. An absent repo
    (a 404) is "not migrated yet", never an error; any other failure raises."""
    if (row := _listing(org).get(repo)) is None:
        return {}
    try:
        branch = branch or row.get("default_branch") or default_branch(org, repo)
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


def _live_runs(org: str, repo: str) -> list[str]:
    """The workflow file (`.github/workflows/x.yml`) of every run of `org/repo` not yet
    finished, off ONE listing of its latest runs filtered here - a run that is queued or
    going is among the newest. Raises when GitHub cannot say."""
    code, out = gh(
        "api",
        f"repos/{org}/{repo}/actions/runs?per_page=50",
        "--jq",
        '.workflow_runs[] | select(.status != "completed") | .path',
    )
    if code != 0:
        raise RuntimeError(f"could not list the runs of {org}/{repo}: {out[:200]}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _alive(targets: list[tuple[str, str]]) -> list[str]:
    """The target repos with a run not yet finished, and each central deploy not yet
    finished: one runs listing per repo and one for the toolkit, where asking per state
    cost five calls per repo and fifteen for the toolkit on every poll."""
    out = [f"{org}/{repo}" for org, repo in targets if _live_runs(org, repo)]
    going = _live_runs(*CENTRAL.split("/", 1))
    out += [
        f"{CENTRAL} ({workflow})"
        for workflow in CENTRAL_REFRESHERS
        # `path` can carry the ref it ran from (`...yml@refs/heads/main`).
        if any(path.split("@")[0].endswith(f"/{workflow}") for path in going)
    ]
    return out


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
    """`(org, repo)` for each live candidate repo that carries a workflow - one Contents
    read of its workflows folder, not a recursive tree of the whole repo."""
    listing = _listing(org)
    return [
        (org, repo)
        for repo in candidates
        if repo in listing
        and not listing[repo].get("archived")
        and top_level(org, repo, WORKFLOWS_DIR)
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
    # Why `do` or `verify` would refuse, read off the org as it is now: the same checks,
    # asked before anything is written, so a preview fails exactly where a run would stop,
    # and a run stops before its pause rather than inside the window. Each check logs its
    # own detail; a reason is one line.
    stops: Callable[[], list[str]] = list


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
        stops: list[str] = []  # the steps a run would stop at
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
            for reason in step.stops():
                log(f"    - will stop here: {reason}")
                stops.append(step.name)
        if stops:
            log_err(
                f"the run would stop at {stops[0]}: fix what is named above by hand, "
                f"then run again. Nothing was written."
            )
            if not preview and pause.window_open():
                pause.load()  # a stopped run's pause: say what is still off
                log_err(pause.stopped())
            return 1
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
SITE_WORKFLOW = "sync-site.yml"


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
    """The Scheduled release, Sync membership and Sync site a pause drops: for one
    semester, scoped to it as its `semester-config` push is; for a course, what the ds01
    timers (and a push of `dsl-course.yml`) send - every semester. Sync site because the
    migration's own commits to `schedule.yml`, `assignments.yml` and `instructors.yml`
    are what would have fired it."""
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
            Tick(
                "sync-site",
                SITE_WORKFLOW,
                (*who, *driver),
                f"Sync site for {semester_org}",
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
        # A Sync site dispatch naming no semester syncs every one.
        Tick("sync-site", SITE_WORKFLOW, driver, "Sync site for every semester"),
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
    try:
        return _settle(targets, before_switch)
    finally:
        # The runs just waited on may have written anything: nothing read before the
        # wait may be answered from memory after it.
        forget_all()


def _settle(targets: Callable[[], list[tuple[str, str]]], before_switch: bool) -> bool:
    # Worked out ONCE: nothing writes while this waits, so the repo set cannot change, and
    # each poll used to re-read every repo's tree to find it again.
    waited_on = targets()
    deadline = clock() + QUIET_WAIT
    while True:
        left = next_tick(clock())
        if before_switch and left < TICK_MARGIN:
            log(f"  a scheduled tick is due in {left:.0f} s - waiting past it")
            sleep(left + QUIET_POLL)
            deadline = clock() + QUIET_WAIT
            continue
        alive = _alive(waited_on)
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
        load_yaml_lines(text) or {}, settings.parse_legacy_assignments(instance)
    )
    bad = [f for f in sched.faults if f.code == NOT_MIGRATED]
    bad += [d for d in sched.dropped if NOT_MIGRATED in d]
    for fault in [] if quiet else bad:
        log_err(
            f"{schedule.SCHEDULE_PATH}: {getattr(fault, 'what', fault)} - fix by hand"
        )
    return not bad


def _instance_clean(text: str, *, legacy_ok: bool = False) -> bool:
    """An `assignments.yml` this engine reads whole: YAML, and no line it refuses -
    except, with `legacy_ok`, the per-assignment blocks the schedule-shape step moves into
    `schedule.yml` (NOT_MIGRATED until it has)."""
    faults = [
        f
        for f in settings.parse_legacy_assignments(text).faults
        if not (legacy_ok and f.code == NOT_MIGRATED)
    ]
    for fault in faults:
        log_err(f"{ASSIGNMENTS_FILE} {fault.label}: {fault.what} - fix by hand")
    return not faults


def _diff(name: str, old: str, new: str) -> list[str]:
    """The changed lines of `name`, as the plan shows them."""
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", n=0, lineterm=""
    )
    return [f"  {line}" for line in lines if not line.startswith(("---", "+++"))]


def _meta_faults(text: str | None) -> list[str]:
    """Each key of a `dsl-course.yml` this engine refuses as NOT_MIGRATED (a retired key,
    an old spelling in the course layer), named."""
    meta = _yaml(text)
    defaults = meta.get(ASSIGNMENT_DEFAULTS_KEY)
    bad = [key for key in RETIRED_COURSE_KEYS if key in meta]
    bad += [
        f"{ASSIGNMENT_DEFAULTS_KEY}.{key}"
        for key in RENAMED_SETTINGS
        if isinstance(defaults, dict) and key in defaults
    ]
    for key in bad:
        log_err(f"{COURSE_CONFIG}: `{key}` is still NOT_MIGRATED")
    return bad


def _grading_clean(text: str | None) -> bool:
    """A `grading_config.yml` read by this engine with no NOT_MIGRATED fault."""
    if text is None:
        return True
    try:
        spec = parse_grading_spec(text)
    except RunSettingInTemplate:
        return False
    return not any(NOT_MIGRATED in d for d in spec.dropped)


# --------------------------------------------------------------- the semester org


class Semester:
    def __init__(self, org: str, course_org: str) -> None:
        self.org, self.course = org, course_org
        self.people = ""
        self.pause = Pause(org, self.targets, course_org, org)
        self.renamed_now: dict[str, str] = {}  # old -> new, renamed by this run
        self.schedule_before: str | None = (
            None  # schedule.yml as the keys step found it
        )

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

    def rename_stops(self) -> list[str]:
        names = set(_listing(self.org))
        return [
            f"{self.org} has both {old} and {new} - resolve by hand"
            for old, new in self.renames_left().items()
            if new in names
        ]

    def rename(self) -> bool:
        names = set(_listing(self.org))
        for old, new in self.renames_left().items():
            if new in names:
                log_err(f"{self.org} has both {old} and {new} - resolve by hand")
                return False
            if not rename_repo(
                self.org, old, new, description=self.description(old) or None
            ):
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
            return old_scaffold(self.org, INSTRUCTORS_FILE, ref)
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
        if live and not {OLD_SEMESTER_POINTER, records.path("pointer")} & set(live):
            files[OLD_SEMESTER_POINTER] = self.old_pointer()
        old_pointer = COURSE_CONFIG in _files(self.org, OLD_POINTER_REPO)
        return moves, files, deletes, old_pointer

    def pointer(self) -> bytes:
        return template("semester/course.yml").format(course=self.course).encode()

    def old_pointer(self) -> bytes:
        """The pointer as the 0.10 layout wrote it (`OLD_SEMESTER_POINTER`)."""
        return (
            old_template("semester/dsl-course.yml").format(course=self.course).encode()
        )

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
        if OLD_SEMESTER_POINTER in files:
            out.append(f"write the course pointer to {OLD_SEMESTER_POINTER}")
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

    def layout_stops(self) -> list[str]:
        """What `layout` refuses: a `people.yml` it cannot convert, a record move onto
        a file that differs (`refuse_clashes` names them)."""
        repo = self.config()
        moves, files, _, _ = self.layout_work()
        out = []
        if INSTRUCTORS_FILE in files and not files[INSTRUCTORS_FILE]:
            out.append(
                f"{OLD_PEOPLE_FILE} in {self.org} cannot be converted faithfully - write "
                f"{INSTRUCTORS_FILE} by hand (docs/05), delete {OLD_PEOPLE_FILE}, then "
                f"run again"
            )
        if refuse_clashes(self.org, repo, _files(self.org, repo), moves):
            out.append(f"a record move in {repo} lands on a file that differs")
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
        existing = settings.parse_legacy_assignments(current)
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
            base = old_scaffold(self.org, ASSIGNMENTS_FILE, ref)
        new = with_instance_keys(base, additions) if additions else base
        return (None if new == current else new), notes

    def keys_done(self) -> bool:
        """The schedule holds none of the 0.9 keys this step rewrites, and the semester
        has its `assignments.yml` reading clean. Whether the schedule reads clean is the
        schedule-shape step's to say: this engine reads only the 0.11 shape."""
        if not self.renamed():
            return False
        text, new = self.keys_text()
        if self.layers_landed():
            return text == new
        instance = self.instance_text()
        return (
            text == new
            and instance is not None
            and _instance_clean(instance, legacy_ok=True)
        )

    def title_notes(self, meta: dict) -> list[str]:
        """`assignments.<key>: title "old" -> "new"` for each `title:` the keys step
        strips that is not the name the assignment takes from now on (its template's,
        `grading_spec.assignment_title`): for faculty to check, nothing is written."""
        entries = meta.get("assignments")
        out = []
        for key, entry in entries.items() if isinstance(entries, dict) else ():
            if not isinstance(entry, dict) or entry.get("title") in (None, ""):
                continue
            repo = str(entry.get("course_source_repo") or "")
            text = repo and get_file_content(
                self.course, repo, GRADING_FILE, ref=SOLUTION_BRANCH
            )
            try:
                spec = parse_grading_spec(text or "")
            except (RunSettingInTemplate, yaml.YAMLError):
                spec = parse_grading_spec("")
            old, new = (
                str(entry["title"]).strip(),
                assignment_title(self.course, repo, spec, str(key)),
            )
            if old != new:
                out.append(
                    f'assignments.{key}: title "{old}" -> "{new}" (the name now comes '
                    f"from the template)"
                )
        return out

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
                current = old_scaffold(self.org, ASSIGNMENTS_FILE, ref)
            out.append(
                f"{CONFIG_REPO}/{ASSIGNMENTS_FILE}: "
                + ("seed the skeleton, and " if seeded else "")
                + "write what the old files said"
            )
            out += _diff(ASSIGNMENTS_FILE, current, instance)
        return out + notes + self.title_notes(_yaml(text))

    def keys_stops(self) -> list[str]:
        """What `keys` refuses and `keys_verified` fails on, over the files as `keys`
        would write them: the first, as the run stops at the first."""
        text, new = self.keys_text()
        instance, _ = self.instance_work()
        if text != new and not entries_kept(text, new):
            return [f"the {schedule.SCHEDULE_PATH} rewrite would lose or add an entry"]
        if instance is not None and not _instance_clean(instance, legacy_ok=True):
            return [f"the {ASSIGNMENTS_FILE} it writes would not read clean"]
        return []

    def keys(self) -> bool:
        text, new = self.keys_text()
        self.schedule_before = text  # what keys_verified compares the commit against
        instance, _ = self.instance_work()
        files = {}
        if text != new:
            if not entries_kept(text, new):
                return False
            files[schedule.SCHEDULE_PATH] = new.encode()
        if instance is not None:
            if not _instance_clean(instance, legacy_ok=True):
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
            and entries_kept(self.schedule_before, text)
            and instance is not None
            and _instance_clean(instance, legacy_ok=True)
        )

    # schedule shape ----------------------------------------------------------
    def _aliases(self, repo: str) -> Mapping[str, str]:
        """A source repo's `materials.yml` folder aliases, as 0.10 inferred kinds by."""
        try:
            return read_materials(self.course, repo).kinds
        except Unusable:
            return {}

    def _reading_list(self, repo: str, path: str) -> str:
        """The READINGS.md (or .txt/.bib) text a copy of `path` from the course's `repo`
        carries: the file itself, or each one directly inside the folder."""
        path = path.strip("/")
        found = [
            p
            for p in sorted(_files(self.course, repo))
            if is_reading_overlay(p)
            and (
                p == path or p.rsplit("/", 1)[0] == path or (not path and "/" not in p)
            )
        ]
        texts = [(get_file_content(self.course, repo, p) or "").strip() for p in found]
        return "\n\n".join(t for t in texts if t)

    def shape_work(
        self,
    ) -> tuple[str | None, str | None, str | None, list[str], list[str]]:
        """`(schedule.yml now, rewritten, assignments.yml rewritten or None, notes,
        stops)`; nothing to rewrite (None) once the schedule reads clean."""
        text = get_file_content(self.org, self.config(), schedule.SCHEDULE_PATH)
        instance = self.instance_text()
        if text is None or _schedule_clean(text, instance, quiet=True):
            return text, None, None, [], []
        new, after, notes, stops = schedule_shape(
            text, instance, self._aliases, self._reading_list
        )
        return text, new, after, notes, stops

    def shape_done(self) -> bool:
        return self.keys_done() and self.shape_work()[1] is None

    def shape_plan(self) -> list[str]:
        if not self.keys_done():
            when = "listed once the keys step has run"
            return [
                f"{CONFIG_REPO}/{schedule.SCHEDULE_PATH}: one block per kind - {when}"
            ]
        text, new, after, notes, _ = self.shape_work()
        out = [
            (
                f"{CONFIG_REPO}/{schedule.SCHEDULE_PATH}: one block per kind (decision "
                f"0038); keys kept, sessions and numbers written, copies' fields renamed"
                + (
                    f"; {ASSIGNMENTS_FILE}'s blocks moved onto their entries"
                    if after
                    else ""
                )
            ),
            # The new lines only: the old file's are faculty's own words and comments,
            # which a public log never quotes - counted instead.
            *(
                line
                for line in _diff(schedule.SCHEDULE_PATH, text or "", new or "")
                if not line.startswith("  -")
            ),
            f"  ({len((text or '').splitlines())} line(s) of the old file replaced)",
        ]
        if after is not None:
            out += _diff(ASSIGNMENTS_FILE, self.instance_text() or "", after)
        return out + [f"  {note}" for note in notes]

    def shape_stops(self) -> list[str]:
        _, new, after, _, stops = self.shape_work()
        if new is None or stops:
            return stops
        if not _schedule_clean(
            new, after if after is not None else self.instance_text()
        ):
            return [f"{schedule.SCHEDULE_PATH} would still say NOT_MIGRATED"]
        return []

    def shape(self) -> bool:
        _, new, after, _, stops = self.shape_work()
        if new is None:
            return True
        if stops or self.shape_stops():
            return False
        files = {schedule.SCHEDULE_PATH: new.encode()}
        if after is not None:
            files[ASSIGNMENTS_FILE] = after.encode()
        return move_files(self.org, self.config(), {}, SHAPE_COMMIT, files=files)

    # config layers -------------------------------------------------------------
    def layers_landed(self) -> bool:
        """`semester.yml` is there and the files it replaced are gone: the steps before
        this one read those files, so each counts as done once they have folded."""
        live = _files(self.org, self.config())
        return SEMESTER_FILE in live and not set(OLD_SEMESTER_FILES) & set(live)

    def layers_work(self) -> dict:
        """Everything the step reads and would write: the old texts, `files` (path ->
        bytes), `delete`, `notes` and `stops`. Empty `files` and `delete` = done."""
        repo = self.config()
        live = _files(self.org, repo)
        old = {
            p: get_file_content(self.org, repo, p)
            for p in (*OLD_SEMESTER_FILES, OLD_SEMESTER_POINTER, roster.STUDENTS_PATH)
            if p in live
        }
        files: dict[str, bytes] = {}
        delete = sorted(p for p in old if p != roster.STUDENTS_PATH)
        notes: list[str] = []
        stops: list[str] = []
        # What the steps above leave, for a preview, which runs none of them.
        laid = {} if self.layers_landed() else self.layout_work()[1]
        if INSTRUCTORS_FILE not in old and laid.get(INSTRUCTORS_FILE):
            old[INSTRUCTORS_FILE] = laid[INSTRUCTORS_FILE].decode()
        if OLD_SEMESTER_POINTER in laid:
            old[OLD_SEMESTER_POINTER] = laid[OLD_SEMESTER_POINTER].decode()
        if not self.layers_landed():
            after = self._assignments_after()
            if after is not None:
                old[ASSIGNMENTS_FILE] = after
        if INSTRUCTORS_FILE in old or ASSIGNMENTS_FILE in old:
            if SEMESTER_FILE in live:
                stops.append(
                    f"{SEMESTER_FILE} is already there beside the files it replaces - "
                    f"fold them into it by hand, then delete them"
                )
            # `materials.yml` `syllabus:` stays where it is until the maintainer rules
            # where a semester's syllabus is declared (its fold is not written).
            syllabus = ""
            inherited = settings.layers(self.course)[2:]
            text, more = semester_layer(
                self.org,
                central_ref_for(self.course),
                old.get(INSTRUCTORS_FILE),
                old.get(ASSIGNMENTS_FILE),
                syllabus,
                inherited,
            )
            stops += more
            lost = semester_layer_kept(
                old.get(INSTRUCTORS_FILE), old.get(ASSIGNMENTS_FILE), syllabus, text
            )
            stops += [f"{SEMESTER_FILE} would lose {what}" for what in lost]
            files[SEMESTER_FILE] = text.encode()
        students = old.get(roster.STUDENTS_PATH)
        legacy_roster = students is not None and (
            roster.LEGACY_EMAIL in (students.lstrip("﻿").split("\n", 1)[0])
            or OLD_TEAMS_PATH in old
        )
        if legacy_roster:
            if roster.SYSTEM_PATH in live:
                stops.append(
                    f"{roster.SYSTEM_PATH} is already there beside an unsplit "
                    f"{roster.STUDENTS_PATH} - split it by hand"
                )
            sched_text = get_file_content(self.org, repo, schedule.SCHEDULE_PATH)
            cols = teams.columns(
                schedule.parse(load_yaml_lines(sched_text or "") or {})
            )
            new_students, new_system = roster.split_legacy(
                students, old.get(OLD_TEAMS_PATH), cols
            )
            stops += roster_kept(
                students, old.get(OLD_TEAMS_PATH), new_students, new_system, cols
            )
            files[roster.STUDENTS_PATH] = new_students.encode()
            files[roster.SYSTEM_PATH] = new_system.encode()
            rows = len(list(csv.DictReader(io.StringIO(new_students))))
            notes.append(
                f"{roster.STUDENTS_PATH}: {rows} row(s); the engine's columns move to "
                f"{roster.SYSTEM_PATH}"
                + (
                    f", {OLD_TEAMS_PATH} becomes its `team:` columns"
                    if OLD_TEAMS_PATH in old
                    else ""
                )
            )
        if OLD_SEMESTER_POINTER in old:
            files[records.path("pointer")] = self.pointer()
        if not files and not delete:
            return {}
        return {
            "old": old,
            "files": files,
            "delete": delete,
            "notes": notes,
            "stops": stops,
        }

    def _assignments_after(self) -> str | None:
        """`assignments.yml` as the keys and schedule-shape steps leave it."""
        after = self.shape_work()[2]
        if after is not None:
            return after
        if not self.keys_done():
            new, _ = self.instance_work()
            if new is not None:
                return _without_block(new, settings.ASSIGNMENTS_BLOCKS) or new
        return self.instance_text()

    def layers_done(self) -> bool:
        return not self.layers_work()

    def layers_plan(self) -> list[str]:
        work = self.layers_work()
        if not work:
            return []
        old = work["old"]
        out = [f"{CONFIG_REPO}: fold the 0.10 files into one per level (decision 0039)"]
        for path, new in work["files"].items():
            if path in (roster.STUDENTS_PATH, roster.SYSTEM_PATH):
                continue  # per-person: counted in the notes, never printed
            out += _diff(path, "", new.decode())
        out += [f"  {note}" for note in work["notes"]]
        out += [f"  delete {path}" for path in work["delete"]]
        if (
            ASSIGNMENTS_FILE in old
            and not settings.parse_legacy_assignments(old[ASSIGNMENTS_FILE]).defaults
        ):
            out.append(f"  ({ASSIGNMENTS_FILE} states no defaults)")
        return out

    def layers_stops(self) -> list[str]:
        return (self.layers_work() or {}).get("stops", [])

    def layers(self) -> bool:
        work = self.layers_work()
        if not work:
            return True
        if work["stops"]:
            return False
        return move_files(
            self.org,
            self.config(),
            {},
            LAYERS_COMMIT,
            files=work["files"],
            delete=work["delete"],
        )

    def layers_verified(self) -> bool:
        """Done, and the new files read clean with this engine's own readers."""
        if not self.layers_done():
            return False
        text = get_file_content(self.org, self.config(), SEMESTER_FILE)
        bad = settings.parse_instance(text).faults
        for fault in bad:
            log_err(f"{SEMESTER_FILE} {fault.label}: {fault.what}")
        return not bad

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
            and self.shape_done()
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
                stops=self.rename_stops,
                rollback="rename each repo back in its Settings (the old name is free)",
            ),
            Step(
                "layout",
                done=self.layout_done,
                plan=self.layout_plan,
                do=self.layout,
                verify=self.layout_verified,
                stops=self.layout_stops,
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
                stops=self.keys_stops,
                rollback=(
                    f"git revert the '{KEYS_COMMIT}' commit in {repo} (it holds "
                    f"{schedule.SCHEDULE_PATH} and {ASSIGNMENTS_FILE})"
                ),
            ),
            Step(
                "schedule shape",
                done=self.shape_done,
                plan=self.shape_plan,
                do=self.shape,
                verify=self.shape_done,
                stops=self.shape_stops,
                rollback=(
                    f"git revert the '{SHAPE_COMMIT}' commit in {repo} (it holds "
                    f"{schedule.SCHEDULE_PATH} and {ASSIGNMENTS_FILE})"
                ),
            ),
            Step(
                "config layers",
                done=self.layers_done,
                plan=self.layers_plan,
                do=self.layers,
                verify=self.layers_verified,
                stops=self.layers_stops,
                rollback=f"git revert the '{LAYERS_COMMIT}' commit in {repo}",
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
        # `{template: its starter mode}`, read off every derivable source on its solution
        # branch - notebooks, often hundreds of KB - so read once per run, not by every
        # done/plan/do/verify of the starter step. Nothing else in a run writes those
        # sources; `write_starters` clears it all the same.
        self._starter_modes: dict[str, str] = {}

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
        bad = _meta_faults(self.meta_text()[0])  # named even when the rewrite is off
        return self.meta_done() and not bad

    def meta_stops(self) -> list[str]:
        if _meta_faults(self.meta_text()[1]):
            return [f"{COURSE_CONFIG} would still say NOT_MIGRATED"]
        return []

    def rewrite_meta(self) -> bool:
        text, new = self.meta_text()
        if text == new:
            return True
        return move_files(
            self.org, ".github", {}, KEYS_COMMIT, files={COURSE_CONFIG: new.encode()}
        )

    # templates ---------------------------------------------------------------
    def template_rows(self) -> list[dict]:
        """Every assignment template's listing row: those with the topic, and the ones the
        topic step gives it (so a preview, where that step has not run, plans the same)."""
        return sorted(
            (
                row
                for row in _listing(self.org).values()
                if is_assignment_template(row) or is_untopicked_template(row)
            ),
            key=lambda row: row["name"],
        )

    def templates(self) -> list[str]:
        """The live templates, by name."""
        return [r["name"] for r in self.template_rows() if not r.get("archived")]

    def untopicked_templates(self) -> list[str]:
        """The live `assignment-*` GitHub templates that do not carry the topic yet:
        discovery finds a template by the topic now (decision 0014). An archived one is
        read-only on GitHub and this migration never writes to one, as everywhere else in
        the module, so the topic step leaves it alone."""
        return [
            r["name"]
            for r in self.template_rows()
            if is_untopicked_template(r) and not r.get("archived")
        ]

    def topic_templates(self) -> bool:
        return all(
            set_repo_topics(
                self.org,
                repo,
                sorted(
                    {*(_listing(self.org)[repo].get("topics") or []), TEMPLATE_TOPIC}
                ),
            )
            for repo in self.untopicked_templates()
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

    def template_stops(self) -> list[str]:
        return [
            f"{repo}@{SOLUTION_BRANCH}/{GRADING_FILE} would still say NOT_MIGRATED"
            for repo, new in self.templates_left().items()
            if not _grading_clean(new)
        ]

    def templates_verified(self) -> bool:
        bad = [r for r in self.templates() if not _grading_clean(self.grading_text(r))]
        for repo in bad:
            log_err(f"{repo}@{SOLUTION_BRANCH}/{GRADING_FILE} is still NOT_MIGRATED")
        return not self.templates_left() and not bad

    # starter (decision 0028) --------------------------------------------------
    def starters_left(self) -> dict[str, tuple[str, str]]:
        """`{template: (its mode, its grading_config.yml with the key)}` for each live
        template whose file parses and has no `starter:` yet. The mode is rule 6's
        reading (`derive.starter_mode`), written down so it never has to be guessed again."""
        out = {}
        for repo in self.templates():
            text = self.grading_text(repo)
            try:
                data = yaml.safe_load(text) if text else None
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict) or "starter" in data:
                continue
            if repo not in self._starter_modes:
                self._starter_modes[repo] = starter_mode(
                    None, self.solution_sources(repo)
                )
            mode = self._starter_modes[repo]
            out[repo] = (mode, f"{text.rstrip()}\n{starter_line(mode)}\n")
        return out

    def solution_sources(self, repo: str) -> dict[str, str]:
        """`{path: text}` for the derivable sources on `repo`'s solution branch."""
        paths = _files(self.org, repo, SOLUTION_BRANCH)
        texts = {
            p: get_file_content(self.org, repo, p, ref=SOLUTION_BRANCH)
            for p in derivable_sources(list(paths))
        }
        return {p: t for p, t in texts.items() if t is not None}

    def write_starters(self) -> bool:
        left = self.starters_left()
        self._starter_modes.clear()
        return all(
            move_files(
                self.org,
                repo,
                {},
                STARTER_COMMIT,
                files={GRADING_FILE: text.encode()},
                branch=SOLUTION_BRANCH,
            )
            for repo, (_, text) in left.items()
        )

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

    def materials_stops(self) -> list[str]:
        """Each repo with a move onto a file that differs (`refuse_clashes` names them)."""
        return [
            f"a system file move in {repo} lands on a file that differs"
            for repo, moves in self.materials_moves().items()
            if refuse_clashes(self.org, repo, _files(self.org, repo), moves)
        ]

    def move_materials(self) -> bool:
        """Every repo's moves checked before the first is written: a clash in one repo
        must not land after another's commit."""
        if self.materials_stops():
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

    # public website ----------------------------------------------------------
    def opencourse_missing(self) -> bool:
        """No `opencourse.yml`, and no `course.yml` it has folded into yet."""
        return (
            get_file_content(self.org, ".github", OPENCOURSE_FILE) is None
            and read_course(self.org).text is None
        )

    def opencourse_seed(self) -> OpenCourse:
        return opencourse_from(
            get_file_content(self.org, pages_repo(self.org), RETIRED_PUBLISH_CONFIG)
        )

    def website_done(self) -> bool:
        return not self.opencourse_missing()

    def website_plan(self) -> list[str]:
        out = []
        if self.opencourse_missing():
            oc = self.opencourse_seed()
            out.append(
                f".github/{OPENCOURSE_FILE}: seeded "
                + (
                    f"on, from {pages_repo(self.org)}/{RETIRED_PUBLISH_CONFIG} "
                    f"(source_repo {oc.source_repo}, readings_mode {oc.readings_mode})"
                    if oc.enabled
                    else "off (no earlier publish settings)"
                )
            )
        return out

    def seed_opencourse(self) -> bool:
        """Seed `opencourse.yml`, create-only: only while it is missing. A materials repo's
        `publish.yml` is not touched (decision 0035 rule 1)."""
        return not self.opencourse_missing() or move_files(
            self.org,
            ".github",
            {},
            PUBLISH_COMMIT,
            files={
                OPENCOURSE_FILE: old_opencourse_text(self.opencourse_seed()).encode()
            },
        )

    # config layers -------------------------------------------------------------
    def layers_texts(self) -> tuple[str | None, str | None]:
        """The 0.10 `dsl-course.yml` and `opencourse.yml`, None where absent."""
        return (
            get_file_content(self.org, ".github", COURSE_CONFIG),
            get_file_content(self.org, ".github", OPENCOURSE_FILE),
        )

    def layers_work(self) -> tuple[str | None, list[str], list[str]]:
        """`(course.yml to write, notes, stops)`; None to write once both old files are
        gone."""
        meta, oc = self.layers_texts()
        if meta is None and oc is None:
            return None, [], []
        # As the keys and public-website steps leave them, for a preview, which runs
        # neither.
        meta = course_config_keys(meta) if meta is not None else None
        if oc is None and read_course(self.org).text is None:
            oc = old_opencourse_text(self.opencourse_seed())
        new, notes, stops = course_layer(meta, oc)
        if read_course(self.org).text is not None:
            stops.append(
                f"{NEW_COURSE_FILE} is already there beside the files it replaces - fold "
                f"them into it by hand, then delete them"
            )
        stops += [
            f"{NEW_COURSE_FILE} would lose {what}"
            for what in course_layer_kept(meta, oc, new)
        ]
        return new, notes, stops

    def layers_done(self) -> bool:
        return self.layers_work()[0] is None

    def layers_plan(self) -> list[str]:
        new, notes, _ = self.layers_work()
        if new is None:
            return []
        meta, oc = self.layers_texts()
        return [
            (
                f".github: fold {COURSE_CONFIG} and {OPENCOURSE_FILE} into "
                f"{NEW_COURSE_FILE} (decision 0039)"
            ),
            *_diff(NEW_COURSE_FILE, "", new),
            *(f"  {note}" for note in notes),
            *(
                f"  delete {path}"
                for path, text in ((COURSE_CONFIG, meta), (OPENCOURSE_FILE, oc))
                if text is not None
            ),
        ]

    def layers_stops(self) -> list[str]:
        return self.layers_work()[2]

    def layers(self) -> bool:
        new, _, stops = self.layers_work()
        if new is None:
            return True
        if stops:
            return False
        return move_files(
            self.org,
            ".github",
            {},
            LAYERS_COMMIT,
            files={NEW_COURSE_FILE: new.encode()},
            delete=[COURSE_CONFIG, OPENCOURSE_FILE],
        )

    def layers_verified(self) -> bool:
        """Done, and course.yml reads with this engine's own readers."""
        if not self.layers_done():
            return False
        try:
            meta = read_course(self.org).mapping()
            opencourse.parse(opencourse.block(meta))
        except (yaml.YAMLError, Unusable) as exc:
            log_err(f"{NEW_COURSE_FILE}: {exc}")
            return False
        return True

    # re-render ---------------------------------------------------------------
    def drift(self) -> list[str]:
        """Every file the course re-render (`seed.refresh`) writes that is not what this
        checkout renders: the `.github` workflows, each content repo's release workflows
        (and a materials repo's system files), each live template's hand-out workflow -
        and a retired workflow still lying in a content repo or template."""
        ref = central_ref_for(self.org)
        semesters = discover_semesters(self.org)
        templates = self.template_rows()
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
                        "assignment_defaults format: -> formats:, "
                        "submit_via: github -> assignment_repo"
                    )
                ],
                do=self.rewrite_meta,
                verify=self.meta_verified,
                stops=self.meta_stops,
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
                            f"submit_via: github -> assignment_repo, run settings out (recorded in .github/{RUN_KEYS_RECORD} "
                            f"for the semesters' {ASSIGNMENTS_FILE})"
                        ),
                        *_diff(GRADING_FILE, self.grading_text(r) or "", new),
                    )
                ],
                do=self.rewrite_templates,
                verify=self.templates_verified,
                stops=self.template_stops,
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
                "template starter",
                done=lambda: not self.starters_left(),
                plan=lambda: [
                    f"{r}@{SOLUTION_BRANCH}/{GRADING_FILE}: starter: {mode} "
                    f"(read from its answer markers)"
                    for r, (mode, _) in self.starters_left().items()
                ],
                do=self.write_starters,
                verify=lambda: not self.starters_left(),
                rollback=(
                    f"git revert the '{STARTER_COMMIT}' commit on each template's "
                    f"{SOLUTION_BRANCH} branch"
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
                stops=self.materials_stops,
                rollback=f"git revert the '{LAYOUT_COMMIT}' commit in each materials repo",
            ),
            Step(
                "public website",
                done=self.website_done,
                plan=self.website_plan,
                do=self.seed_opencourse,
                verify=self.website_done,
                rollback=f"git revert the '{PUBLISH_COMMIT}' commit in {dotgithub}",
            ),
            Step(
                "assignment topic",
                done=lambda: not self.untopicked_templates(),
                plan=lambda: [
                    f"{repo}: add the topic {TEMPLATE_TOPIC}"
                    for repo in self.untopicked_templates()
                ],
                do=self.topic_templates,
                verify=lambda: not self.untopicked_templates(),
                rollback=f"remove the {TEMPLATE_TOPIC} topic from the templates",
            ),
            Step(
                "config layers",
                done=self.layers_done,
                plan=self.layers_plan,
                do=self.layers,
                verify=self.layers_verified,
                stops=self.layers_stops,
                rollback=f"git revert the '{LAYERS_COMMIT}' commit in {dotgithub}",
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
        text = read_semester(org).pointer.text
        if text is None:
            text = get_file_content(org, CONFIG_REPO, OLD_SEMESTER_POINTER)
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
    # Every read live: a migration plans, waits for other runs to finish (`settle`), then
    # writes on top of what they wrote.
    parser = CLIParser(description=__doc__, read_once=False)
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


def _list_semester_grades_rename() -> None:
    """0.11.0: the semester grades file's old name joins the layout step's moves, so a
    semester that returned no marks since lists and moves it. (Return marks renames it on
    its own first real run.)"""
    SEMESTER_MOVES[records.RETIRED_SEMESTER_GRADES] = records.path("semester_gradebook")


_list_semester_grades_rename()


if __name__ == "__main__":
    sys.exit(main())
