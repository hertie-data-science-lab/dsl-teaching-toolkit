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

Semester org, in order: preflight, pause, rename repos, layout, keys, topic, re-render,
unpause, status. Course org: preflight, pause, registry, .system/, dsl-course.yml keys,
template keys, materials files, re-render, unpause, status.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

import yaml

from . import records, seed, status
from .central import PAUSE_VARIABLE
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
    SEMESTER_TOPIC,
    SOLUTION_BRANCH,
    SYLLABUS_SAMPLE_FILE,
    SYLLABUS_SESSIONS_FILE,
)
from .discovery import (
    OLD_SEMESTERS_PATH,
    SEMESTERS_PATH,
    central_ref_for,
    list_org_repos,
)
from .faults import NOT_MIGRATED
from .gh_contents import blob_sha, get_file_content, move_files, repo_blob_shas
from .ghcli import gh
from .grades import GRADING_FILE, sync_team_lock
from .log import CLIParser, add_preview_flag, log, log_err, log_ok, log_step
from .profile_readme import update_profile_readme
from .repos import default_branch, set_repo_topics
from .schedule import SCHEDULE_PATH
from .welcome import (
    config_system_files,
    join_files,
    refresh_config_system_files,
    refresh_join_workflows,
    refresh_semester_pointer,
    template,
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


# ------------------------------------------------------------------ key rewrites
# Line rewrites, not YAML round trips: these are instructor files, and their comments and
# layout are theirs. Each returns the text unchanged when there is nothing to rewrite.

_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*):")


def schedule_keys(text: str) -> str:
    """`schedule.yml`: `cohort_dest_*` -> `semester_dest_*` anywhere, and `type:` ->
    `kind:` on the entries of `releases:` and `events:` (an assignment's `type:` is its
    individual/group shape and stays)."""
    out, section = [], ""
    for line in text.split("\n"):
        if top := _TOP_KEY.match(line):
            section = top.group(1)
        line = re.sub(
            r"^(\s*(?:-\s+)?)cohort_dest_(repo|path):", r"\1semester_dest_\2:", line
        )
        if section in ("releases", "events"):
            line = re.sub(r"^(\s+(?:-\s+)?)type:", r"\1kind:", line)
        out.append(line)
    return "\n".join(out)


def formats_line(line: str, indent: str = "") -> str:
    """One `format: x` line (at `indent`) as `formats: [x]`; any other line unchanged."""
    match = re.match(rf"^{indent}format:(\s*)([^#\n]*?)(\s*#.*)?$", line)
    if not match:
        return line
    value = match.group(2).strip()
    listed = value if value.startswith("[") else f"[{value}]"
    return f"{indent}formats: {listed}{match.group(3) or ''}"


def grading_config_keys(text: str) -> str:
    """A template's `grading_config.yml`: the top-level `format:` becomes `formats:`."""
    return "\n".join(formats_line(line) for line in text.split("\n"))


def course_config_keys(text: str) -> str:
    """The course `dsl-course.yml`: `cohort_defaults:` -> `semester_defaults:`, and the
    `format:` under `assignment_defaults:` -> `formats:`."""
    out, section = [], ""
    for line in text.split("\n"):
        if top := _TOP_KEY.match(line):
            section = top.group(1)
            if section == "cohort_defaults":
                line = "semester_defaults:" + line[len("cohort_defaults:") :]
        elif section == "assignment_defaults":
            indent = re.match(r"^(\s+)format:", line)
            if indent:
                line = formats_line(line, indent.group(1))
        out.append(line)
    return "\n".join(out)


def registry_keys(text: str) -> str:
    """The course registry: its `cohorts:` key becomes `semesters:`."""
    return re.sub(r"(?m)^cohorts:", "semesters:", text)


_ROLE_KEYS = {"instructors": "instructor", "teaching_assistants": "teaching_assistant"}


def people_to_instructors(text: str) -> str | None:
    """`people.yml` (a `people:` mapping of role -> list) as `instructors.yml` (one
    `instructors:` list, `role:` on every entry), line by line so comments survive. None
    when the file is not in the shape the toolkit seeded - that one is converted by hand."""
    lines = text.split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "people:")
    except StopIteration:
        return None
    out, role = [*lines[:start], "instructors:"], None
    for line in lines[start + 1 :]:
        if _TOP_KEY.match(line):
            return None  # a second top-level key: not the seeded shape
        key = re.match(r"^  ([a-z_]+):\s*(\[\s*\])?\s*$", line)
        if key:
            if key.group(1) not in _ROLE_KEYS:
                return None
            role = _ROLE_KEYS[key.group(1)]
            continue
        body = line.removeprefix("  ")
        out.append(body)
        if re.match(r"^  -(\s|$)", body):
            if role is None:
                return None
            out.append(f"    role: {role}")
    return "\n".join(out)


def same_people(old: str, new: str) -> bool:
    """Whether `new` (instructors.yml) names exactly the people of `old` (people.yml),
    field for field, each with the role their old list gave them."""
    before = (yaml.safe_load(old) or {}).get("people") or {}
    want = [
        {**entry, "role": _ROLE_KEYS[key]}
        for key in _ROLE_KEYS
        for entry in before.get(key) or []
    ]
    got = (yaml.safe_load(new) or {}).get("instructors") or []
    return sorted(want, key=repr) == sorted(got, key=repr)


# ------------------------------------------------------------------ GitHub, narrowly


def _listing(org: str) -> dict[str, dict]:
    return {row["name"]: row for row in list_org_repos(org)}


def _topics(listing: dict[str, dict]) -> set[str]:
    return set((listing.get(".github") or {}).get("topics") or [])


def _files(org: str, repo: str, branch: str = "") -> dict[str, str]:
    return repo_blob_shas(org, repo, branch or default_branch(org, repo))


def _pause_value(org: str) -> str | None:
    code, out = gh(
        "api", f"orgs/{org}/actions/variables/{PAUSE_VARIABLE}", "--jq", ".value"
    )
    return out.strip() if code == 0 else None


def _pause(org: str) -> bool:
    if _pause_value(org) is None:
        code, out = gh(
            "api", "--method", "POST", f"orgs/{org}/actions/variables",
            "-f", f"name={PAUSE_VARIABLE}", "-f", "value=true", "-f", "visibility=all",
        )  # fmt: skip
    else:
        code, out = gh(
            "api", "--method", "PATCH", f"orgs/{org}/actions/variables/{PAUSE_VARIABLE}",
            "-f", "value=true",
        )  # fmt: skip
    if code != 0:
        log_err(f"could not pause {org}: {out[:200]}")
    return code == 0


def _unpause(org: str) -> bool:
    if _pause_value(org) is None:
        return True
    code, out = gh(
        "api", "--method", "DELETE", f"orgs/{org}/actions/variables/{PAUSE_VARIABLE}"
    )
    if code != 0:
        log_err(f"could not unpause {org}: {out[:200]}")
    return code == 0


def _runs_alive(org: str) -> int:
    """How many workflow runs are queued or running in `org`'s `.github` - the repo every
    scheduled and dispatched job of a course runs from. -1 when that cannot be read."""
    total = 0
    for state in ("in_progress", "queued"):
        code, out = gh(
            "api",
            f"repos/{org}/.github/actions/runs?status={state}",
            "--jq",
            ".total_count",
        )
        if code != 0 or not out.strip().isdigit():
            return -1
        total += int(out.strip())
    return total


def _rename(org: str, old: str, new: str) -> bool:
    code, out = gh(
        "api", "--method", "PATCH", f"repos/{org}/{old}", "-f", f"name={new}"
    )
    if code != 0:
        log_err(f"could not rename {org}/{old} to {new}: {out[:200]}")
    return code == 0


def _yaml(text: str | None) -> dict:
    data = yaml.safe_load(text or "") if text else None
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ steps


@dataclass
class Step:
    name: str
    done: Callable[[], bool]
    plan: Callable[[], list[str]]
    do: Callable[[], bool]
    verify: Callable[[], bool]
    rollback: str
    # The pause and the unpause bracket the work: with no work left they are skipped too,
    # so a run over a migrated org changes nothing at all.
    bracket: bool = False


def run(org: str, steps: list[Step], preview: bool) -> int:
    """Print the plan; then (not in preview) do, verify, stop at the first failure."""
    work = [s for s in steps if not s.bracket]
    idle = all(s.done() for s in work[:-1])  # the last is the status check, always run

    def done(step: Step) -> bool:
        return idle if step.bracket else step.done()

    log_step(f"Migration plan for {org}")
    for step in steps:
        if done(step):
            log(f"  {step.name}: already migrated")
            continue
        log(f"  {step.name}:")
        for line in step.plan():
            log(f"    - {line}")
    if preview:
        log_ok("PREVIEW - nothing was written. Run again with --no-preview to migrate.")
        return 0
    for step in steps:
        if done(step):
            log(f"  [skip] {step.name}: already migrated")
            continue
        log_step(step.name)
        if not step.do() or not step.verify():
            log_err(
                f"{step.name} did not verify - stopped here. Rollback: {step.rollback}"
            )
            return 1
        log_ok(f"{step.name}: done and verified")
    log_ok(f"{org} is migrated")
    return 0


def _pause_step(orgs: list[str]) -> Step:
    both = " and ".join(orgs)

    def quiet() -> bool:
        alive = [o for o in orgs if _runs_alive(o) != 0]
        if alive:
            log_err(
                f"a workflow run is queued or running in {', '.join(alive)}/.github - "
                f"let it finish, then run the migration again"
            )
        return not alive

    return Step(
        f"pause automation ({PAUSE_VARIABLE})",
        done=lambda: all(_pause_value(o) == "true" for o in orgs),
        plan=lambda: [f"set the org variable {PAUSE_VARIABLE}=true in {both}"],
        do=lambda: all(_pause(o) for o in orgs),
        verify=lambda: all(_pause_value(o) == "true" for o in orgs) and quiet(),
        rollback=f"delete the org variable {PAUSE_VARIABLE} in {both}",
        bracket=True,
    )


def _unpause_step(orgs: list[str]) -> Step:
    both = " and ".join(orgs)
    return Step(
        "unpause automation",
        done=lambda: all(_pause_value(o) is None for o in orgs),
        plan=lambda: [f"delete the org variable {PAUSE_VARIABLE} in {both}"],
        do=lambda: all(_unpause(o) for o in orgs),
        verify=lambda: all(_pause_value(o) is None for o in orgs),
        rollback=f"set {PAUSE_VARIABLE}=true in {both} again",
        bracket=True,
    )


def _status_step(course_org: str, semester_org: str | None) -> Step:
    repo = CONFIG_REPO if semester_org else ".github"
    org = semester_org or course_org

    def not_migrated() -> int | None:
        doc = get_file_content(org, repo, records.path("status"))
        if doc is None:
            return None
        return sum(
            NOT_MIGRATED in json.dumps(p) for p in json.loads(doc).get("problems") or []
        )

    def clean() -> bool:
        count = not_migrated()
        if count:
            log_err(
                f"status.json still reports {count} NOT_MIGRATED problem(s) in {org}"
            )
        return count == 0

    # Never "already migrated": a status.json the OLD engine wrote (moved in by the layout
    # step) knows nothing of NOT_MIGRATED, so the check always rewrites it with this one.
    return Step(
        "status",
        done=lambda: False,
        plan=lambda: [
            f"write {repo}/{records.path('status')} and expect zero NOT_MIGRATED"
        ],
        do=lambda: status.refresh(course_org, semester_org) == 0,
        verify=clean,
        rollback="read the problems in status.json; each names the file to fix",
    )


# --------------------------------------------------------------- the semester org


class Semester:
    def __init__(self, org: str, course_org: str) -> None:
        self.org, self.course = org, course_org

    def config(self) -> str:
        """The config repo under whichever name it has right now."""
        return CONFIG_REPO if CONFIG_REPO in _listing(self.org) else OLD_CONFIG_REPO

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
        return True

    def renamed(self) -> bool:
        names = set(_listing(self.org))
        return all(
            new in names and old not in names for old, new in REPO_RENAMES.items()
        )

    # layout -------------------------------------------------------------------
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
                text = get_file_content(self.org, repo, OLD_PEOPLE_FILE) or ""
                files[INSTRUCTORS_FILE] = (people_to_instructors(text) or "").encode()
        if records.path("pointer") not in live:
            files[records.path("pointer")] = (
                template("semester/dsl-course.yml")
                .format(course=self.course, org=self.org)
                .encode()
            )
        old_pointer = COURSE_CONFIG in _files(self.org, OLD_POINTER_REPO)
        return moves, files, deletes, old_pointer

    def layout_done(self) -> bool:
        moves, files, deletes, old_pointer = self.layout_work()
        return not (moves or files or deletes or old_pointer)

    def layout_plan(self) -> list[str]:
        moves, files, deletes, old_pointer = self.layout_work()
        out = [
            f"move {len(moves)} record file(s) under {records.SYSTEM_DIR}/ (one commit)"
        ]
        if INSTRUCTORS_FILE in files:
            out.append(
                f"{OLD_PEOPLE_FILE} -> {INSTRUCTORS_FILE} (one list, a role each)"
            )
        if records.path("pointer") in files:
            out.append(f"write the course pointer to {records.path('pointer')}")
        samples = [p for p in deletes if p.endswith(SAMPLE_SUFFIX)]
        if samples:
            out.append(f"delete {len(samples)} *{SAMPLE_SUFFIX} file(s)")
        if old_pointer:
            out.append(f"delete {OLD_POINTER_REPO}/{COURSE_CONFIG} (the old pointer)")
        return out

    def layout(self) -> bool:
        repo = self.config()
        moves, files, deletes, old_pointer = self.layout_work()
        if INSTRUCTORS_FILE in files and not files[INSTRUCTORS_FILE]:
            log_err(
                f"{OLD_PEOPLE_FILE} in {self.org} is not in the seeded shape - write "
                f"{INSTRUCTORS_FILE} by hand (docs/05), then run again"
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
        moved = fold(set(before), SEMESTER_MOVES)
        # Every marker at its new path, byte for byte, and nowhere else.
        for old, new in moved.items():
            if old in live or live.get(new) != before[old]:
                log_err(
                    f"{repo}: a record did not arrive intact under {records.SYSTEM_DIR}/"
                )
                return False
        old = getattr(self, "people", "")
        if old and OLD_PEOPLE_FILE not in live:
            new = get_file_content(self.org, repo, INSTRUCTORS_FILE) or ""
            if not same_people(old, new):
                log_err(
                    f"{INSTRUCTORS_FILE} does not name the people {OLD_PEOPLE_FILE} did"
                )
                return False
        return self.layout_done()

    # keys -------------------------------------------------------------------
    def keys_text(self) -> tuple[str | None, str | None]:
        text = get_file_content(self.org, self.config(), SCHEDULE_PATH)
        return text, (schedule_keys(text) if text is not None else None)

    def keys_done(self) -> bool:
        text, new = self.keys_text()
        return text == new

    def keys(self) -> bool:
        text, new = self.keys_text()
        if text == new:
            return True
        return move_files(
            self.org,
            self.config(),
            {},
            KEYS_COMMIT,
            files={SCHEDULE_PATH: new.encode()},
        )

    # topic -------------------------------------------------------------------
    def topics(self) -> set[str]:
        return _topics(_listing(self.org))

    def retopic(self) -> bool:
        topics = (self.topics() - {OLD_SEMESTER_TOPIC}) | {SEMESTER_TOPIC}
        return set_repo_topics(self.org, ".github", sorted(topics))

    # re-render ---------------------------------------------------------------
    def drift(self) -> list[str]:
        ref = central_ref_for(self.course)
        out = []
        for repo, want in (
            (CONFIG_REPO, config_system_files(ref)),
            (JOIN_REPO, join_files(self.org)),
        ):
            live = _files(self.org, repo)
            out += [
                f"{repo}/{p}"
                for p, body in want.items()
                if live.get(p) != blob_sha(body)
            ]
        return out

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
            _pause_step([self.org, self.course]),
            Step(
                "rename repos",
                done=self.renamed,
                plan=lambda: [
                    f"rename {o} -> {n}" for o, n in self.renames_left().items()
                ],
                do=self.rename,
                verify=self.renamed,
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
                    f"{SCHEDULE_PATH}: cohort_dest_* -> semester_dest_*, type -> kind"
                ],
                do=self.keys,
                verify=self.keys_done,
                rollback=f"git revert the '{KEYS_COMMIT}' commit in {repo}",
            ),
            Step(
                "topic",
                done=lambda: (
                    SEMESTER_TOPIC in self.topics()
                    and OLD_SEMESTER_TOPIC not in self.topics()
                ),
                plan=lambda: [
                    f".github topic {OLD_SEMESTER_TOPIC} -> {SEMESTER_TOPIC}"
                ],
                do=self.retopic,
                verify=lambda: (
                    SEMESTER_TOPIC in self.topics()
                    and OLD_SEMESTER_TOPIC not in self.topics()
                ),
                rollback=f"set the .github topic back to {OLD_SEMESTER_TOPIC}",
            ),
            Step(
                "re-render",
                done=lambda: not self.drift(),
                plan=lambda: [f"re-write {len(self.drift())} SYSTEM-OWNED file(s)"],
                do=self.rerender,
                verify=lambda: not self.drift(),
                rollback="the rollbacks of the steps above, in reverse",
            ),
            _unpause_step([self.org, self.course]),
            _status_step(self.course, self.org),
        ]


# ------------------------------------------------------------------ the course org


class Course:
    def __init__(self, org: str) -> None:
        self.org = org

    def registry(self) -> tuple[str | None, str | None]:
        return (
            get_file_content(self.org, ".github", OLD_SEMESTERS_PATH),
            get_file_content(self.org, ".github", SEMESTERS_PATH),
        )

    def migrate_registry(self) -> bool:
        old, new = self.registry()
        files = (
            {}
            if new is not None
            else {SEMESTERS_PATH: registry_keys(old or "").encode()}
        )
        return move_files(
            self.org,
            ".github",
            {},
            LAYOUT_COMMIT,
            files=files,
            delete=[OLD_SEMESTERS_PATH],
        )

    def registry_done(self) -> bool:
        old, new = self.registry()
        return old is None and (new is None or "cohorts" not in _yaml(new))

    def dotgithub_moves(self) -> dict[str, str]:
        return fold(set(_files(self.org, ".github")), COURSE_MOVES)

    def meta_text(self) -> tuple[str | None, str | None]:
        text = get_file_content(self.org, ".github", COURSE_CONFIG)
        return text, (course_config_keys(text) if text is not None else None)

    def meta_done(self) -> bool:
        text, new = self.meta_text()
        return text == new

    def rewrite_meta(self) -> bool:
        text, new = self.meta_text()
        if text == new:
            return True
        return move_files(
            self.org, ".github", {}, KEYS_COMMIT, files={COURSE_CONFIG: new.encode()}
        )

    def templates(self) -> list[str]:
        return sorted(
            name
            for name, row in _listing(self.org).items()
            if row.get("isTemplate") and name.startswith("assignment-")
        )

    def templates_left(self) -> dict[str, str]:
        """`{template: its rewritten grading_config.yml}` for each that needs it."""
        out = {}
        for repo in self.templates():
            text = get_file_content(self.org, repo, GRADING_FILE, ref=SOLUTION_BRANCH)
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

    def materials_moves(self) -> dict[str, dict[str, str]]:
        return {
            repo: moves
            for repo in sorted(_listing(self.org))
            if repo.startswith(MATERIALS_REPO_PREFIX)
            and (moves := fold(set(_files(self.org, repo)), MATERIALS_MOVES))
        }

    def move_materials(self) -> bool:
        return all(
            move_files(self.org, repo, moves, LAYOUT_COMMIT)
            for repo, moves in self.materials_moves().items()
        )

    def drift(self) -> list[str]:
        want = seed.github_workflow_files(self.org, central_ref_for(self.org))
        live = _files(self.org, ".github")
        return [p for p, body in want.items() if live.get(p) != blob_sha(body)]

    def steps(self) -> list[Step]:
        dotgithub = f"{self.org}/.github"
        return [
            _pause_step([self.org]),
            Step(
                "registry",
                done=self.registry_done,
                plan=lambda: [
                    f".github/{OLD_SEMESTERS_PATH} -> {SEMESTERS_PATH}, cohorts: -> semesters:"
                ],
                do=self.migrate_registry,
                verify=self.registry_done,
                rollback=f"git revert the '{LAYOUT_COMMIT}' commit in {dotgithub}",
            ),
            Step(
                f"{records.SYSTEM_DIR}/ in .github",
                done=lambda: not self.dotgithub_moves(),
                plan=lambda: [
                    f"move {len(self.dotgithub_moves())} file(s) under {records.SYSTEM_DIR}/"
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
                    "cohort_defaults: -> semester_defaults:, assignment_defaults format: -> formats:"
                ],
                do=self.rewrite_meta,
                verify=self.meta_done,
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
                verify=lambda: not self.templates_left(),
                rollback=f"git revert the '{KEYS_COMMIT}' commit on each template's {SOLUTION_BRANCH} branch",
            ),
            Step(
                "materials files",
                done=lambda: not self.materials_moves(),
                plan=lambda: [
                    f"{repo}: move {len(m)} system file(s) under {records.SYSTEM_DIR}/"
                    for repo, m in self.materials_moves().items()
                ],
                do=self.move_materials,
                verify=lambda: not self.materials_moves(),
                rollback=f"git revert the '{LAYOUT_COMMIT}' commit in each materials repo",
            ),
            Step(
                "re-render",
                done=lambda: not self.drift(),
                plan=lambda: [
                    f"Refresh actions from this checkout ({len(self.drift())} workflow(s) differ)"
                ],
                do=lambda: seed.refresh(self.org) == 0,
                verify=lambda: not self.drift(),
                rollback="the rollbacks of the steps above, in reverse",
            ),
            _unpause_step([self.org]),
            _status_step(self.org, None),
        ]


# ------------------------------------------------------------------ preflight


def _pointer_course(org: str, listing: dict[str, dict]) -> str:
    """The course org a semester points at, from whichever pointer it has now."""
    if CONFIG_REPO in listing:
        text = get_file_content(org, CONFIG_REPO, records.path("pointer"))
        if text is not None:
            return str(_yaml(text).get("course") or "")
    return str(
        _yaml(get_file_content(org, OLD_POINTER_REPO, COURSE_CONFIG)).get("course")
        or ""
    )


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
        if _runs_alive(org) != 0:
            log_err(
                f"a workflow run is queued or running in {org}/.github - wait for it"
            )
            return None
        return Course(org)
    if not topics & {OLD_SEMESTER_TOPIC, SEMESTER_TOPIC}:
        log_err(
            f"{org}'s .github carries neither {COURSE_HUB_TOPIC} nor a semester topic"
        )
        return None
    config = listing.get(CONFIG_REPO) or listing.get(OLD_CONFIG_REPO)
    if (
        config is None
        or config.get("archived")
        or all(r.get("archived") for r in listing.values())
    ):
        log_err(
            f"{org} is archived (or has no config repo) - archived semesters are never touched"
        )
        return None
    course = _pointer_course(org, listing)
    if not course:
        log_err(f"{org}'s course pointer names no course org")
        return None
    if get_file_content(course, ".github", OLD_SEMESTERS_PATH) is not None:
        log_err(f"{course} is not migrated yet - migrate the course org first")
        return None
    if _runs_alive(course) != 0:
        log_err(
            f"a workflow run is queued or running in {course}/.github - wait for it"
        )
        return None
    return Semester(org, course)


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument("org", help="The course org or semester org to migrate")
    add_preview_flag(parser, "Print the plan and write nothing (default).")
    args = parser.parse_args()
    target = preflight(args.org)
    if target is None:
        return 1
    log(
        f"  central ref of the course: {central_ref_for(getattr(target, 'course', args.org))}"
    )
    return run(args.org, target.steps(), args.preview)


if __name__ == "__main__":
    sys.exit(main())
