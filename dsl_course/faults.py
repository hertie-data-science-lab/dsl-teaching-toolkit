"""dsl-course faults -- ONE type for everything a hand-edited config file gets wrong.

Every file faculty edit by hand can be wrong in a way the toolkit can detect and a human
must fix: a source the release plan names and nobody staged, a `;`-delimited students.csv,
a people.yml handle with a typo in it, a schedule entry the parser had to drop. Each of
those used to reach people its own way - a rung ladder here, a `log_err` line in a public
run log there, a red X on an unattended cron somewhere else - and three of the four
reached nobody.

`ConfigFault` is what they all are, so one engine can carry them: the digest issue that
holds the current list (`config_digest`), the mail that goes to the person git names
(`notify`), and the run summary that says what was dropped. A parser's job is to build
one; deciding who hears about it, and how loudly, belongs to the fault itself
(`severity`) and to the two notifiers.

TWO CLOCKS, and `fires` is which:

- `fires` set - a SOURCE the plan cites, which bites at a moment. The same missing folder
  is a note in August and a failure the day before the lecture, so it climbs the ladder
  below as its moment approaches.
- `fires` None - an IMMEDIATE fault: a line the toolkit cannot read at all. Waiting
  changes nothing about it, so it sits flat at the notify bar (WARNING) from the moment it
  exists and the age ladder that reminds people about it is the digest's, not the fault's.

This module holds no I/O and imports nothing that does, because both halves of the engine
have to see it: `schedule` builds source faults, `notify` and `config_digest` consume
every kind, and `Severity` has to be the SAME ladder on both sides of that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, IntEnum

from .course import CONFIG_REPO

# How close a source's moment has to be before anything is said, and how much louder each
# step is. Deliberately tight - a day, half a day, a quarter of a day - because a source
# is staged in minutes once somebody knows, and a week of warnings is a week of ignoring
# them.
SOURCE_WARN_WINDOW = timedelta(hours=24)
SOURCE_URGENT_WINDOW = timedelta(hours=12)
SOURCE_CRITICAL_WINDOW = timedelta(hours=6)


class Severity(IntEnum):
    """How loud a fault is. ORDERED, and that is the point: every consumer wants to
    compare (worst of a run, has this escalated, is it past the notify bar), and as bare
    strings each of those had to spell the ladder out again through a lookup table."""

    ADVISORY = 0
    WARNING = 1
    URGENT = 2
    CRITICAL = 3
    MISSED = 4

    def __str__(self) -> str:
        return self.name.lower()


# The rung at which anything is said at all - the digest issue opens, its comment goes out
# and the mail beside it is addressed. Below it the fault is real and listed, but a session
# nobody has written yet is the normal state of a term planned months ahead, so it earns no
# notification. Here rather than in either notifier because both answer to it and they must
# answer to the SAME one.
NOTIFY_FROM = Severity.WARNING


def hours(window: timedelta) -> int:
    """A window in whole hours. Public because every surface that names one - a rung
    heading, a subject suffix, a prose blurb, the commit comment - formats it from the
    constant rather than typing the number, so moving a rung cannot leave a stale 24h in
    somebody's inbox."""
    return int(window.total_seconds() // 3600)


def zone_name(when: datetime) -> str:
    """The zone as faculty wrote it in schedule.yml (`Europe/Berlin`), not the abbreviation
    in force that week.

    A notification that says 08:00 without saying whose 08:00 is a notification about
    nothing, and `%Z` answers `CEST` - which is not a string anybody can look up against
    the `timezone:` line they typed."""
    return getattr(when.tzinfo, "key", "") or when.strftime("%Z")


class FaultKind(Enum):
    """What is WRONG with a SOURCE, as against which key to go and edit.

    The field cannot carry this: a path that is absent and a path a `.releaseignore`
    withholds are both `course_source_path`, and they want opposite instructions - push
    the files, or stop holding back the ones already there. Sniffed off the field name and
    a bool flag, each surface grew its own copy of the guess.

    Only a source fault has one, which is also what tells the two clocks apart - see
    `ConfigFault.is_source`."""

    MISSING_REPO = "missing repo"
    MISSING_PATH = "missing path"
    WITHHELD = "withheld"


_CREATE_REPO = (
    "create the repo named on {at} in {course_org} and push its files, or correct the "
    "line above."
)
_CREATE_TEMPLATE = (
    "create the assignment template repo named on {at} in {course_org} and push the "
    "starter files to it, or correct the line above."
)
# The one sentence that would put each SOURCE fault right, keyed on the three things it
# actually depends on: the KIND, whether the entry hands out an assignment template (a
# repo to create, not a folder to fill), and whether the moment has already passed - once
# a release has fired, "fix it by Wednesday" is no longer the instruction. A table rather
# than a chain of `if`s, so a fourth kind cannot quietly inherit a sentence meant for
# another. Combinations the check cannot produce are absent: only a deploy names a path,
# and a withheld path caps at WARNING and so never fires.
_FIX: dict[tuple[FaultKind, bool, bool], str] = {
    (FaultKind.WITHHELD, False, False): "remove the pattern, or change {field}.",
    (FaultKind.MISSING_REPO, False, False): _CREATE_REPO,
    (FaultKind.MISSING_REPO, False, True): _CREATE_REPO,
    (FaultKind.MISSING_REPO, True, False): _CREATE_TEMPLATE,
    (FaultKind.MISSING_REPO, True, True): _CREATE_TEMPLATE,
    (FaultKind.MISSING_PATH, False, False): (
        "push the materials to that folder in {course_org}/{repo}, or correct the path "
        "on the line above."
    ),
    (FaultKind.MISSING_PATH, False, True): (
        "push the materials to that folder now - the next 15-minute tick releases them. "
        "Nothing else is needed."
    ),
}

# What the cohort LOSES while an immediate fault stands, one sentence per file. The mail
# leads with it ("Until they are fixed: ...") because "students.csv has 1 entry the
# toolkit cannot use" says nothing about whether anybody's term is affected, and the
# answer differs sharply per file: a bad roster header stops every enrolment, a bad
# teams.csv row stops one.
CONSEQUENCE = {
    "schedule.yml": (
        "that entry is not scheduled: nothing releases, hands out or grades from it"
    ),
    "people.yml": "this person has no access and is not notified",
    "students.csv": (
        "the whole roster is skipped: nobody new is enrolled or sent a code"
    ),
    "teams.csv": "that row is ignored: the team is not created or the member not added",
}

# What to do about a CSV whose header the toolkit cannot read. The same sentence for every
# such file, because the failure is the same one every time: Excel in a German locale hands
# back a `;`-delimited export (or a BOM), which DictReader reads as one nameless column and
# every consumer then reads as an empty file.
CSV_HEADER_FIX = (
    "save the file as comma-separated UTF-8 with the header shown in the template"
)

# The FALLBACK fix sentence per file, for a fault whose parser had nothing more specific
# to say. A parser that can name the row or the key sets `fix_text` instead - "fix row 4"
# beats "fix the file" every time - and this is what the rest get.
FIX = {
    "schedule.yml": (
        "correct the value on the line above; the run summary lists what the parser "
        "expected"
    ),
    "people.yml": "fix the handle/email on the line above",
    "students.csv": CSV_HEADER_FIX,
    "teams.csv": CSV_HEADER_FIX,
}


@dataclass
class ConfigFault:
    """One thing in one hand-edited file that the toolkit cannot use, and when it bites.

    Field order is Part I's `SourceFault` order, unchanged, because that is the shape
    every source-fault call site (and the test factory) builds positionally. Everything
    Part II added has a default, and an immediate fault leaves the source-only three
    (`kind`, `repo`, `path`) alone."""

    where: str  # the YAML path or row, e.g. "releases.lecture-2", "row 4"
    what: str  # what is wrong, e.g. "`cm/lectures/02_b` does not exist yet"
    # What makes a source fault actionable, and the line between the two clocks - see the
    # module docstring. None = nothing pins this to a moment.
    fires: datetime | None = None
    # The key or column to go and edit - `course_source_path`, `github_handle`. Naming the
    # field is what turns "something is wrong with lecture-2" into an instruction, and it
    # is part of `key`, so a fault that omitted it would quietly take another's identity
    # in the digest's state.
    field: str = ""
    # Which of the three things went wrong with a SOURCE - see `FaultKind`. None for an
    # immediate fault, which is also how the two are told apart (`is_source`).
    kind: FaultKind | None = None
    # The loudest this fault may ever get. A MISSING source climbs the whole ladder,
    # because the copy will not ship and nobody meant that. A source a `.releaseignore`
    # withholds is a decision faculty already made, so it caps at WARNING: it is listed,
    # and it never earns anyone an email at 24h or a "this did not ship" once its moment
    # has passed.
    ceiling: Severity = Severity.MISSED
    # The line of the file this is written on, as the parser saw it. Every surface turns
    # it into `schedule.yml:36` and a deep link, because the entry name alone still leaves
    # faculty scrolling a file they wrote in August. For a CSV it is the row's own line
    # (`reader.line_num`), header included.
    lineno: int | None = None
    # SOURCE ONLY. The source repo the entry names (`course-materials-f2026`) and the path
    # inside it. `what` spells them inside a `<org>/<repo>/<path>` phrase, which reads well
    # and parses badly - the fault mail has to name `<course_org>/<repo>` as the place to
    # go and stage the thing.
    repo: str = ""
    path: str = ""
    # The config file this fault is IN, and the repo that holds it: `schedule.yml` in
    # `classroom-config`. Distinct from `repo`/`path` above, which are the source a
    # schedule entry POINTS AT. Together they make the citation, the deep link and the
    # blame query that decides who is told.
    file: str = ""
    in_repo: str = CONFIG_REPO
    # This fault's own fix sentence, where the parser knows one better than the file's
    # (see FIX). Lower-case and unpunctuated at the front, so a caller can prefix `fix:`.
    fix_text: str = ""

    @property
    def is_source(self) -> bool:
        """Whether this is a source the plan cites (as against a line that cannot be
        read). `kind` is the discriminator: only a source fault has one, and everything
        that differs between the two clocks - the ladder for an undated fault, the fix
        table, the mail that carries it - hangs off this one question."""
        return self.kind is not None

    @property
    def is_assignment(self) -> bool:
        """Whether the entry hands out an assignment template rather than deploying a
        folder. Asked once here: two surfaces sniffed `where` for the prefix themselves,
        which is a parse of a display string in the middle of deciding what to tell
        somebody."""
        return self.where.startswith("assignments.")

    @property
    def key(self) -> str:
        """A stable identity for this fault across runs, so a digest can tell a fault that
        ESCALATED from one that is merely still there. Deliberately excludes `fires`: an
        entry whose date faculty push back is the same fault, at a new distance.

        The PATH is part of it, because two deploys under one entry are two faults: keyed
        on the entry alone the second inherited the first's recorded rung, so neither
        could appear, escalate or clear on its own. The FILE is not, because there is one
        digest issue per file and a key only ever means anything inside one of them."""
        if not self.path:
            return f"{self.where}.{self.field}"
        return f"{self.where}[{self.path}].{self.field}"

    @property
    def at(self) -> str:
        """`schedule.yml:36`, or a bare `schedule.yml` when the line is not known.

        The citation every surface shows - the mail, the digest body and comment, the CLI
        report, the fix sentence - so all of them cite the file the same way."""
        return f"{self.file}:{self.lineno}" if self.lineno else self.file

    def link(self, org: str = "") -> str | None:
        """The GitHub URL of the exact line to edit, or None when the line - or the org it
        is in - is not known.

        `main` is hard-coded because that is the only branch anything reads these files
        from, the cohort's own workflows included."""
        if not org or not self.lineno or not self.file:
            return None
        return (
            f"https://github.com/{org}/{self.in_repo}/blob/main/{self.file}"
            f"#L{self.lineno}"
        )

    def cite(self, org: str = "") -> str:
        """`at` for a MARKDOWN surface: a link to the exact line where the line and the
        org are both known, plain code where either is not.

        Every markdown surface - the digest body, its transition comment, the commit
        comment on the push - cites the file through this, so the fix is one click from
        wherever somebody first hears about it rather than a scroll through a file they
        wrote in August."""
        url = self.link(org)
        return f"[`{self.at}`]({url})" if url else f"`{self.at}`"

    @property
    def due(self) -> str:
        """The moment this fault bites, zone and all - see `zone_name`."""
        if self.fires is None:
            return "no date (tbc)"
        return f"{self.fires:%a %d %b %Y, %H:%M} {zone_name(self.fires)}"

    @property
    def due_short(self) -> str:
        """`due` without the year - the subject-line form, where every character is paid
        for by the sender's name beside it."""
        if self.fires is None:
            return "no date (tbc)"
        return (
            f"{self.fires:%a} {self.fires.day} {self.fires:%b} "
            f"{self.fires:%H:%M} {zone_name(self.fires)}"
        )

    @property
    def moment(self) -> str:
        """What the date IS: a release ships, an assignment is handed out."""
        return "handout" if self.is_assignment else "release"

    @property
    def consequence(self) -> str:
        """What the cohort loses while this stands - see CONSEQUENCE. Empty for a file
        with no sentence written for it yet, which reads as "say nothing" rather than as a
        guess about somebody's term."""
        return CONSEQUENCE.get(self.file, "")

    def severity(self, now: datetime) -> Severity:
        """How loud this should be at `now` - see the SOURCE_*_WINDOW constants.

        A source fault whose moment has already PASSED is MISSED and stays there: the copy
        did not ship, and going quiet once the lecture is over is the one thing that must
        not happen. The rungs decide who is TOLD (the digest, and the mail beside it);
        none of them touches an exit code, because a line nobody has written yet is a
        content fault and the red X belongs to the run itself."""
        if self.fires is None:
            # The two clocks meet here. An IMMEDIATE fault is at the notify bar the moment
            # it exists - nothing about it improves by waiting, and the digest's age
            # ladder does the reminding. A SOURCE with no date is the opposite case: an
            # undated `tbc` entry can never fire, so it can never escalate either.
            flat = Severity.ADVISORY if self.is_source else Severity.WARNING
            return min(flat, self.ceiling)
        left = self.fires - now
        if left <= timedelta(0):
            rung = Severity.MISSED
        elif left <= SOURCE_CRITICAL_WINDOW:
            rung = Severity.CRITICAL
        elif left <= SOURCE_URGENT_WINDOW:
            rung = Severity.URGENT
        elif left <= SOURCE_WARN_WINDOW:
            rung = Severity.WARNING
        else:
            rung = Severity.ADVISORY
        return min(rung, self.ceiling)

    def fix(self, course_org: str = "", rung: Severity | None = None) -> str:
        """What would put this right, in one sentence, lower-case and unpunctuated at the
        front so a caller can prefix it with `fix:`.

        Here rather than in the notifier because BOTH channels say it - the digest issue
        body and the mail - and a fault whose issue and whose email disagree about the
        remedy is worse than either on its own.

        An immediate fault carries its own sentence (or takes its file's); a source fault
        reads it off the table, which depends on the rung because "fix it by Wednesday"
        stops being an instruction once Wednesday has gone."""
        if not self.is_source:
            return self.fix_text or FIX.get(self.file, "")
        fired = rung is Severity.MISSED
        # Falls back to the not-yet-fired sentence for a (kind, entry, rung) triple the
        # table does not list. Unreachable today; a KeyError inside a release cron is not
        # the way to find out that stopped being true.
        template = (
            _FIX.get((self.kind, self.is_assignment, fired))
            or _FIX[self.kind, False, False]
        )
        return template.format(
            at=self.at, course_org=course_org, repo=self.repo, field=self.field
        )

    def line(self, cite: str | None = None) -> str:
        """The one-line form, everywhere: the entry, the field, the line, what is wrong and
        when it bites.

        It names the FIELD as well as the entry, because "something is wrong with
        lecture-2" is not an instruction. Dash-separated, in this order, because the
        commit-time validator lists these lines verbatim (see `schedule.source_comment`).

        `cite` replaces the plain `schedule.yml:36` with the caller's own rendering of it -
        the markdown deep link, on the surfaces that are markdown. The rest of the line is
        the same line, because a reader comparing the commit comment with the run summary
        is reading about the same fault."""
        at = f" - {cite or self.at}" if self.lineno else ""
        when = f"fires {self.due}" if self.fires else self.due
        return f"{self.where} -> {self.field}{at} - {self.what} - {when}"


def header_fault(file: str, missing: list[str]) -> ConfigFault:
    """The fault a CSV whose header cannot be read produces - one per file, not per row,
    because a header nobody can read costs the whole file.

    Built here rather than in each reader so students.csv, teams.csv and the sheets say the
    same thing. It names the columns that are MISSING (a constant of the toolkit's) and not
    the ones it found: a file whose header row was deleted has cell values there, and this
    text travels to an email and an issue - see the privacy rule in CLAUDE.md."""
    return ConfigFault(
        "header",
        f"header lacks {', '.join(missing)} - a semicolon-delimited export looks "
        f"like this",
        file=file,
        field=", ".join(missing),
        lineno=1,
        fix_text=CSV_HEADER_FIX,
    )


def csv_row(lineno: int | None) -> str:
    """`row 4` - how a CSV fault names WHERE it is, in the one place that decides it.

    A row number and a column name, and never a cell: every faculty workflow runs in a
    public repo, and a students.csv cell is a name, an address or an enrolment code. The
    header is row 1, which is what `csv.reader.line_num` already calls it."""
    return f"row {lineno}" if lineno else "row"
