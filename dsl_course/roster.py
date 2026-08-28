"""dsl-course roster -- read the per-cohort students.csv.

The single durable roster artifact is a PRIVATE per-cohort `students.csv`, kept in
the cohort org's `classroom-config` repo. Columns:

    hertie_email,name,github_handle,github_id,enrol_code,role

These are the columns the engine READS; a roster may carry any others faculty want
(a registrar id, a lecture section, a notes column) and they are carried through
untouched - every write path here addresses cells by column NAME, never by position
(`enrol_codes.fill_enrol_codes_in_csv` and the welcome repo's Join handler both), so an
extra column is neither read nor lost.

`github_handle` / `github_id` are blank until the student onboards (the `welcome` Join
issue fills them); a row with a blank handle is enrolled-but-not-yet-onboarded and is
skipped by provisioning.

`role` splits the cohort into `enrolled` (the default - full participants) and `auditor`
(read-only: released materials, but no assignment repos and no gradebook). A roster
written before the column existed has no `role` cell at all, so a missing or blank value
means `enrolled` - never break onboarding for a deployed cohort's roster.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .course import CONFIG_REPO
from .log import log_err
from .utils import get_file_content, require_csv_header, strip_bom

ROSTER_PATH = "students.csv"
ROLE_ENROLLED = "enrolled"
ROLE_AUDITOR = "auditor"
FIELDS = (
    "hertie_email",
    "name",
    "github_handle",
    "github_id",
    "enrol_code",
    "role",
)
# The columns no roster may lack. `enrol_code` and `role` were added later and stay
# optional so an older cohort keeps parsing; these two are what every consumer keys on.
REQUIRED_FIELDS = ("hertie_email", "github_handle")


@dataclass
class Student:
    hertie_email: str
    name: str
    github_handle: str
    github_id: str
    enrol_code: str = (
        ""  # random non-PII token the bot generates + emails; pasted to enrol
    )
    role: str = ROLE_ENROLLED  # `enrolled` (default) or `auditor` (read-only)

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


def normalise_role(value: str) -> str:
    """Map a raw `role` cell to `enrolled` / `auditor`.

    Blank (or a column that isn't there at all - a roster seeded before the column
    existed) means `enrolled`, so no deployed cohort breaks. Anything unrecognised also
    reads as `enrolled`, but says so on stderr rather than silently mis-classifying."""
    role = value.strip().lower()
    if role == ROLE_AUDITOR:
        return ROLE_AUDITOR
    if role and role != ROLE_ENROLLED:
        log_err(f"unknown roster role '{value.strip()}' - treating as {ROLE_ENROLLED}")
    return ROLE_ENROLLED


def parse(text: str) -> list[Student]:
    """Parse students.csv text into Student rows.

    Tolerant of a roster written before a column existed: a missing `enrol_code` or
    `role` column is fine (blank / `enrolled` respectively)."""
    rows = []
    reader = csv.DictReader(io.StringIO(strip_bom(text)))
    require_csv_header(reader.fieldnames, REQUIRED_FIELDS, ROSTER_PATH)
    for row in reader:
        values = {f: (row.get(f) or "").strip() for f in FIELDS}
        values["role"] = normalise_role(values["role"])
        rows.append(Student(**values))
    return rows


def enrolled(students: list[Student]) -> list[Student]:
    """The full participants - the only rows that get assignment repos + gradebooks."""
    return [s for s in students if s.is_enrolled]


def dump(students: list[Student]) -> str:
    """Serialise rows back to students.csv text (header + one row per student)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(FIELDS)
    for s in students:
        writer.writerow([getattr(s, f) for f in FIELDS])
    return out.getvalue()


def load(cohort_org: str) -> list[Student] | None:
    """Fetch + parse students.csv from the cohort's PRIVATE classroom-config repo.

    Returns None (after logging why) when the file can't be fetched at all - callers
    can then distinguish "roster missing/unreadable" (an error) from a roster that
    exists but has no rows yet (a valid state for a freshly bootstrapped cohort)."""
    content = get_file_content(cohort_org, CONFIG_REPO, ROSTER_PATH)
    if content is None:
        log_err(
            f"Could not find {ROSTER_PATH} in {cohort_org}/{CONFIG_REPO} - "
            f"bootstrap the cohort first (bootstrap_course --cohort)."
        )
        return None
    return parse(content)


def load_path(path: str) -> list[Student]:
    """Parse a local students.csv (for running outside Actions)."""
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())
