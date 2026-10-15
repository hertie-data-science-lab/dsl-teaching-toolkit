"""dsl-course roster -- the semester's students: who they are, and who they are on GitHub.

Two PRIVATE files in the semester's `semester-config` repo, joined on `email` (decision
0039 rule 4):

    students.csv          INSTRUCTOR-OWNED   email,name,role,team:4,team:6
    .system/roster.csv    SYSTEM-OWNED       email,github_handle,github_id,enrol_code,code_sent_at

`students.csv` is the one faculty edit: one row per student, plus one `team:<assignment
number>` column per team assignment (a team is the rows sharing a cell value; blank = no
team). Any other column faculty add (a registrar id, a section, notes) is theirs: the
engine never reads it, and the one engine write to this file (the Join-team form setting a
team cell) addresses cells by column NAME, so an extra column is neither read nor lost.

`.system/roster.csv` is the engine's: the enrol-code send writes `enrol_code` and
`code_sent_at`, the Join-course issue writes `github_handle` / `github_id`, the relink
moves a renamed account's row. Nobody edits it by hand, so a push of `students.csv` never
races the engine's writes.

THE reader of both (`load`, `parse`): every module that needs a student, a handle for an
email or an email for a handle comes through here (`by_email`, `by_handle`).

`role` splits the semester into `enrolled` (the default - full participants) and `auditor`
(read-only: released materials, but no assignment repos and no gradebook). A missing or
blank value means `enrolled`.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import legacy, records, run
from .course import CONFIG_REPO
from .faults import ConfigFault, csv_row_fault
from .gh_contents import dump_csv, get_file_content, read_csv
from .log import log_err

STUDENTS_PATH = "students.csv"
SYSTEM_PATH = records.path("roster")
ROLE_ENROLLED = "enrolled"
ROLE_AUDITOR = "auditor"
# students.csv's own columns, in the order the seed ships them.
FIELDS = ("email", "name", "role")
REQUIRED_FIELDS = ("email",)
# One column per team assignment: `team:<the assignment's number>`.
TEAM_PREFIX = "team:"
# .system/roster.csv: every column, all the engine's.
SYSTEM_FIELDS = ("email", "github_handle", "github_id", "enrol_code", "code_sent_at")


@dataclass
class Student:
    email: str
    name: str
    github_handle: str = ""
    github_id: str = ""
    # random non-PII token the engine generates + emails; pasted to enrol
    enrol_code: str = ""
    role: str = ROLE_ENROLLED  # `enrolled` (default) or `auditor` (read-only)
    code_sent_at: str = ""  # when the code email went out; blank = not yet emailed
    # `{team column suffix ("4"): team name, casefolded}` - the non-blank team cells.
    teams: dict[str, str] = field(default_factory=dict)

    @property
    def onboarded(self) -> bool:
        return bool(self.github_handle.strip())

    @property
    def is_auditor(self) -> bool:
        """Read-only participant: released materials, no assignments, no gradebook."""
        return self.role.strip().lower() == ROLE_AUDITOR

    @property
    def is_enrolled(self) -> bool:
        """Full participant - the default, so a blank role never locks anyone out."""
        return not self.is_auditor


def known_role(value: str) -> bool:
    """Whether this `role` cell is one the engine understands - blank included."""
    return value.strip().lower() in ("", ROLE_ENROLLED, ROLE_AUDITOR)


def normalise_role(value: str) -> str:
    """Map a raw `role` cell to `enrolled` / `auditor`. Blank, absent or unrecognised
    reads as `enrolled`; `parse` says so, once, through the row's fault."""
    return ROLE_AUDITOR if value.strip().lower() == ROLE_AUDITOR else ROLE_ENROLLED


def team_column(suffix: object) -> str:
    """The students.csv column of one team assignment: `team:4`."""
    return f"{TEAM_PREFIX}{suffix}"


_ONE_STUDENT = "the two rows are one student to everything downstream"
_ONE_CODE = "neither row is emailed its code until one is blanked"


def shared_codes(students: list[Student]) -> set[str]:
    """The `enrol_code`s (casefolded) that more than one student holds. The Join issue
    binds a code to the FIRST row that has it, so mailing one to the second student hands
    them a seat that is not theirs."""
    seen = Counter(s.enrol_code.casefold() for s in students if s.enrol_code)
    return {code for code, n in seen.items() if n > 1}


def _key(email: str) -> str:
    return email.strip().casefold()


def parse_system(
    text: str | None, faults: list[ConfigFault] | None = None
) -> dict[str, dict[str, str]]:
    """`.system/roster.csv` as `{casefolded email: {column: value}}`. None or empty text
    is nobody onboarded yet. A handle or a code on two rows is recorded in `faults` (the
    engine's own file, so a bug to report rather than faculty's to fix); the first row of
    an email wins."""
    if not text or not text.strip():
        return {}
    out: dict[str, dict[str, str]] = {}
    seen: dict[str, dict[str, int]] = {"github_handle": {}, "enrol_code": {}}
    reader = read_csv(text, REQUIRED_FIELDS, SYSTEM_PATH, faults)
    for row in reader:
        lineno = reader.line_num
        values = {f: (row.get(f) or "").strip() for f in SYSTEM_FIELDS}
        email = _key(values["email"])
        if not email or email in out:
            continue
        if faults is not None:
            for column, cost in (
                ("github_handle", _ONE_STUDENT),
                ("enrol_code", _ONE_CODE),
            ):
                cell = values[column].casefold()
                if not cell:
                    continue
                if cell in seen[column]:
                    faults.append(
                        csv_row_fault(
                            SYSTEM_PATH,
                            lineno,
                            column,
                            f"this {column} is already on row {seen[column][cell]} - "
                            f"{cost}",
                        )
                    )
                seen[column].setdefault(cell, lineno)
        out[email] = values
    return out


def parse(
    text: str,
    system_text: str | None = None,
    faults: list[ConfigFault] | None = None,
) -> list[Student]:
    """students.csv joined with `.system/roster.csv` (`system_text`, None = nobody has a
    code or a handle yet) into Student rows, in students.csv's order.

    `faults` collects what a human has to fix, as well as logging it: an unreadable header
    (recorded by `read_csv`, which then raises), a `role` nobody can act on, an email on two
    rows (one student's repos, grades and code would land on the other's row). A caller
    that passes nothing gets the rows alone."""
    system = parse_system(system_text, faults)
    rows = []
    emails: dict[str, int] = {}
    reader = read_csv(text, REQUIRED_FIELDS, STUDENTS_PATH, faults)
    team_columns = [
        c for c in reader.fieldnames or [] if c.strip().startswith(TEAM_PREFIX)
    ]
    for row in reader:
        lineno = reader.line_num
        values = {f: (row.get(f) or "").strip() for f in FIELDS}
        if not any(values.values()) and not any(
            (row.get(c) or "").strip() for c in team_columns
        ):
            continue
        if not known_role(values["role"]):
            # The ROW, never the cell: a name with an unquoted comma shifts the cells, so
            # the `role` cell can hold a student's name or address, and this line lands
            # in the public run log of every faculty workflow that reads the roster.
            fault = csv_row_fault(
                STUDENTS_PATH,
                lineno,
                "role",
                f"unrecognised role - the row is treated as {ROLE_ENROLLED} "
                f"(expected {ROLE_ENROLLED} or {ROLE_AUDITOR})",
            )
            log_err(f"{STUDENTS_PATH} {fault.where}: {fault.what}")
            if faults is not None:
                faults.append(fault)
        email = _key(values["email"])
        if faults is not None and email:
            if email in emails:
                faults.append(
                    csv_row_fault(
                        STUDENTS_PATH,
                        lineno,
                        "email",
                        f"this email is already on row {emails[email]} - {_ONE_STUDENT}",
                    )
                )
            emails.setdefault(email, lineno)
        sys_row = system.get(email, {}) if email else {}
        teams = {
            c.strip()[len(TEAM_PREFIX) :].strip(): (row.get(c) or "").strip().casefold()
            for c in team_columns
            if (row.get(c) or "").strip()
        }
        rows.append(
            Student(
                email=values["email"],
                name=values["name"],
                role=normalise_role(values["role"]),
                github_handle=sys_row.get("github_handle", ""),
                github_id=sys_row.get("github_id", ""),
                enrol_code=sys_row.get("enrol_code", ""),
                code_sent_at=sys_row.get("code_sent_at", ""),
                teams=teams,
            )
        )
    return rows


def enrolled(students: list[Student]) -> list[Student]:
    """The full participants - the only rows that get assignment repos + gradebooks."""
    return [s for s in students if s.is_enrolled]


def by_email(students: list[Student]) -> dict[str, Student]:
    """`{casefolded email: Student}` - the first row of an email wins."""
    out: dict[str, Student] = {}
    for s in students:
        if s.email.strip():
            out.setdefault(_key(s.email), s)
    return out


def by_handle(students: list[Student]) -> dict[str, Student]:
    """`{casefolded github_handle: Student}` over the onboarded rows - GitHub logins are
    case-insensitive. The first row of a handle wins."""
    out: dict[str, Student] = {}
    for s in students:
        if s.onboarded:
            out.setdefault(s.github_handle.strip().casefold(), s)
    return out


@run.per_run(lambda semester_org: run.scope(semester_org, CONFIG_REPO, STUDENTS_PATH))
def _students_text(semester_org: str) -> str | None:
    """students.csv's text, read ONCE per semester per process. The TEXT is memoised
    rather than `load`'s rows, so every caller gets its own parse. Held by the run
    (`run.per_run`): a write to the file forgets it. An absent roster is a fault mailed
    to the semester, so a 404 is asked again before it counts."""
    return get_file_content(
        semester_org, CONFIG_REPO, STUDENTS_PATH, confirm_absent=True
    )


@run.per_run(lambda semester_org: run.scope(semester_org, CONFIG_REPO, SYSTEM_PATH))
def _system_text(semester_org: str) -> str | None:
    """`.system/roster.csv`'s text, read once per semester per process (see
    `_students_text`)."""
    return get_file_content(semester_org, CONFIG_REPO, SYSTEM_PATH)


def reread() -> None:
    """Forget the memoised roster texts, so the next `load` reads them again. For a caller
    that REPORTS the send columns (`status.json` counts codes sent) at the end of a run
    that began before a send."""
    _students_text.cache_clear()
    _system_text.cache_clear()


def load(
    semester_org: str, faults: list[ConfigFault] | None = None
) -> list[Student] | None:
    """students.csv joined with `.system/roster.csv`, from the semester's PRIVATE
    semester-config repo.

    Returns None (after logging why) when students.csv is absent - callers can then tell
    "roster missing" (an error) from a roster that exists but has no rows yet. An absent
    students.csv is a fault like any unusable row: nobody is enrolled and nobody is sent a
    code. An absent `.system/roster.csv` is nobody onboarded yet. A read that FAILED does
    not come through here at all: `get_file_content` returns None only for a 404 and raises
    on everything else."""
    content = _students_text(semester_org)
    if content is None:
        log_err(
            f"Could not find {STUDENTS_PATH} in {semester_org}/{CONFIG_REPO} - "
            f"bootstrap the semester first (bootstrap_course --semester)."
        )
        if faults is not None:
            faults.append(
                ConfigFault(
                    STUDENTS_PATH,
                    "this file is missing, so no student is enrolled and no enrolment "
                    "code is sent",
                    file=STUDENTS_PATH,
                    fix_text=(
                        "add the file with the header shown in the template, one row "
                        "per student"
                    ),
                )
            )
        return None
    return parse(content, _system_text(semester_org), faults)


def load_path(path: str) -> list[Student]:
    """Parse a local students.csv, joined with the `.system/roster.csv` beside it in the
    same checkout when there is one (for running outside Actions)."""
    students = Path(path)
    system = students.parent / SYSTEM_PATH
    return parse(
        students.read_text(encoding="utf-8"),
        system.read_text(encoding="utf-8") if system.is_file() else None,
    )


# ------------------------------------------------------------- writing roster.csv


def fill_system(
    text: str | None,
    column: str,
    values: dict[str, str],
    replacing: str | None = "",
) -> str:
    """`.system/roster.csv` with `column` set for each email in `values` (`{email:
    value}`, matched casefolded), a row appended for an email it does not carry yet
    (unless only a held value is being replaced).

    Only cells whose current text is `replacing` are written, which is what makes every
    caller idempotent: the default (blank) means a code already issued is never rotated and
    a row already marked as emailed is never re-stamped. `None` writes whatever the cell
    holds. Every other row and cell is carried through as it stands."""
    rows: list[dict[str, str]] = []
    fieldnames = list(SYSTEM_FIELDS)
    if text and text.strip():
        reader = read_csv(text, REQUIRED_FIELDS, SYSTEM_PATH)
        fieldnames += [f for f in reader.fieldnames or [] if f not in fieldnames]
        rows = [dict(r) for r in reader]
    at = {}
    for i, r in enumerate(rows):
        at.setdefault(_key(r.get("email") or ""), i)
    for email, value in values.items():
        if not _key(email):
            continue
        i = at.get(_key(email))
        if i is None:
            if replacing not in ("", None):
                continue  # nothing there to replace
            rows.append({"email": email.strip()})
            i = at[_key(email)] = len(rows) - 1
        cell = (rows[i].get(column) or "").strip()
        if replacing is None or cell == replacing:
            rows[i][column] = value
    return dump_csv(fieldnames, ([r.get(f) or "" for f in fieldnames] for r in rows))


# ------------------------------------------------------------- the 0.11.0 migration

# The single students.csv before 0.11.0 and the teams.csv beside it (`legacy`).
LEGACY_EMAIL = legacy.old_name("email", legacy.ROSTER_FILES)
LEGACY_SYSTEM_COLUMNS = tuple(
    r.old for r in legacy.rows(legacy.ROSTER_FILES) if r.new == SYSTEM_PATH
)
LEGACY_TEAMS_PATH = next(
    r.old for r in legacy.rows(legacy.ROSTER_FILES) if r.old.endswith(".csv")
)


def _column_order(column: str) -> tuple:
    """Numbered team columns by number, then any keyed by name."""
    suffix = column[len(TEAM_PREFIX) :]
    return (0, int(suffix), "") if suffix.isdigit() else (1, 0, suffix)


def split_legacy(
    students_text: str, teams_text: str | None, numbers: dict[str, int]
) -> tuple[str, str]:
    """`(students.csv, .system/roster.csv)` out of a pre-0.11.0 students.csv
    (`hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at` plus any
    faculty column) and its teams.csv (`assignment,team,github_handle`).

    `hertie_email` becomes `email`; the system columns move to the roster file (one row
    per student carrying any of them); faculty's own columns stay where they were; each
    assignment teams.csv names becomes a `team:<number>` column (`numbers` = schedule key
    -> assignment number, matched casefolded; an assignment with no number keeps its key,
    `team:<key>`), filled for the student whose handle the team row names. A teams.csv row
    whose handle is on no roster row is dropped: it could never have been granted a team.
    Pure: text in, text out."""
    reader = csv.DictReader(io.StringIO(students_text.lstrip("﻿")))
    old = list(reader.fieldnames or [])
    rows = [dict(r) for r in reader]
    handle_row = {
        (r.get("github_handle") or "").strip().casefold(): i
        for i, r in enumerate(rows)
        if (r.get("github_handle") or "").strip()
    }
    folded = {k.casefold(): n for k, n in numbers.items()}
    cells: dict[str, dict[int, str]] = {}
    if teams_text and teams_text.strip():
        for t in csv.DictReader(io.StringIO(teams_text.lstrip("﻿"))):
            key = (t.get("assignment") or "").strip().casefold()
            team = (t.get("team") or "").strip().casefold()
            at = handle_row.get((t.get("github_handle") or "").strip().casefold())
            if not (key and team) or at is None:
                continue
            column = team_column(folded.get(key, key))
            cells.setdefault(column, {}).setdefault(at, team)
    team_cols = sorted(cells, key=_column_order)
    header = [
        "email" if c == LEGACY_EMAIL else c
        for c in old
        if c not in LEGACY_SYSTEM_COLUMNS
    ]
    for c in FIELDS:
        if c not in header:
            header.append(c)
    header += team_cols

    def value(row: dict, i: int, column: str) -> str:
        if column in cells:
            return cells[column].get(i, "")
        return row.get(LEGACY_EMAIL if column == "email" else column) or ""

    students = dump_csv(
        header, ([value(r, i, c) for c in header] for i, r in enumerate(rows))
    )
    system_rows = [
        [(r.get(LEGACY_EMAIL) or "").strip()]
        + [(r.get(c) or "").strip() for c in LEGACY_SYSTEM_COLUMNS]
        for r in rows
        if (r.get(LEGACY_EMAIL) or "").strip()
        and any((r.get(c) or "").strip() for c in LEGACY_SYSTEM_COLUMNS)
    ]
    return students, dump_csv(SYSTEM_FIELDS, system_rows)
