"""dsl-course gradebook -- each student's private `grades-<handle>` repo, and the record of
what reached it.

Provisioning the repos, what a student is shown (`student_view`, the README), the
semester grades file, and the distributed-records ledger (`distributed.csv`) that makes a
re-run say nothing twice.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml

from . import records, roster
from .access import FACULTY_READ_ACCESS, grant_faculty
from .course import (
    GRADEBOOK_PREFIX,
    identifier,
    row_name,
)
from .discovery import (
    exists_in,
    listing_by_name,
    listing_row,
)
from .gh_contents import (
    blob_sha,
    dump_csv,
    put_file,
    read_csv,
)
from .grading_sheet import (
    _SEP,
    INFO_KEY,
    LATE_PENALTY_KEY,
    QUESTION_FEEDBACK_KEY,
    SheetSpec,
    _people,
    _plain,
    _units,
    effective_penalty,
    final_grade,
    is_blank,
    penalty_rate,
    score_total,
)
from .grading_spec import (
    total_points,
)
from .log import (
    log,
    log_err,
    log_ok,
    log_person,
    log_step,
)
from .repos import (
    add_collaborator,
    create_repo,
    set_repo_topics,
)
from .setting_readers import (
    as_decimal,
)

GRADEBOOK_DIR = records.path(
    "gradebook"
)  # what has been sent, beside the retired files
# RETIRED. The old per-student notification marker, named here for one reason only: the
# migration in `_read_distributed` reads it once and deletes it in the same commit that
# writes `distributed.csv`, which records every channel rather than just the email.
NOTIFIED_PATH = f"{GRADEBOOK_DIR}/notified.csv"
GRADEBOOK_PERMISSION = (
    "pull"  # a student READS their gradebook; the sheet is the source
)
SEMESTER_CSV_NAME = records.path(
    "semester_gradebook"
)  # the semester grades file: the wide faculty-only glance view

# What a gradebook says before its student has been marked in anything. The legend names
# the keys `STUDENT_VIEW_KEYS` allows and no others: this is the first file a student opens,
# and promising them a team score or their own adjustment - neither of which a gradebook
# ever shows - contradicts the one thing the page is for. Replaced wholesale by
# `render_readme` on the first distribute.
_STARTER_README = (
    "# Your gradebook\n\n"
    "This private repository is viewable only by you. Grades and feedback for each "
    "piece of assessment appear in `grades.yml` as the course progresses.\n\n"
    "## What each field means\n\n"
    "| Field | Meaning |\n"
    "| --- | --- |\n"
    "| `final_grade` | Your mark for that assignment. This is the authoritative one. |\n"
    "| `score` | Individual assignments only: the marks behind that total. |\n"
    "| `feedback` | Your marker's feedback on your own work. |\n"
    "| `feedback_per_question` | Feedback on each question, where there is any. |\n"
    "| `submitted`, `days_late`, `penalty` | When your work was recorded, and what any "
    "late days cost. |\n"
    "| `team` | Group assignments only: the team you submitted with. |\n"
    "| `team_feedback` | Group assignments only: feedback shared with the whole team. |\n"
)


# --------------------------------------------------------------------------- pure core


def render_yaml(book: dict) -> str:
    """Serialise one student's gradebook to YAML text (insertion order preserved)."""
    return yaml.safe_dump(book, sort_keys=False, allow_unicode=True)


# ------------------------------------------------------------- what a student is shown

# The ONLY keys that may reach a student, in the order a view writes them. An ALLOWLIST,
# not a redaction list: a key added to the sheet next term - a fact the toolkit starts
# recording, a column a grader invents for themselves - is invisible here until someone
# names it. A denylist would have to be right about every key anyone ever adds; this has
# to be right about nine.
#
# Deliberately absent: `notes_not_shared_with_students`, every `info:` fact but
# `submitted` and `days_late` (`autograde`, `completion`, `contributions`), and - because
# a gradebook shows the final grade and never its disaggregation - the team's score and
# the member's own `adjustment_individual`. Those last two are INPUTS to the number the
# student sees, not results, and the gradebook is the ONE place a mark is written: the
# team's shared feedback reaches each member through `team_feedback` below, and the
# adjustment stays between a member and their grader.
STUDENT_VIEW_KEYS = (
    "final_grade",  # derived on output, never stored - the authoritative mark
    "score",  # individual assignments only: what the grader typed, per question or flat
    "max_points",  # the declared maxima summed, so a 40 reads as "40 / 50"
    "feedback",  # feedback_individual
    QUESTION_FEEDBACK_KEY,  # per question: the student's own, or the team's
    "submitted",  # info.submitted, as a person reads a date
    "days_late",  # info.days_late
    "penalty",  # what those days cost, e.g. "-20%"
    "team",  # group assignments only
    "team_feedback",  # feedback_group
)

# The month names are the toolkit's own, never the runner's locale: otherwise a
# German-locale Actions runner writes "Okt" into one semester's gradebook and "Oct" into
# the next one's.
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def spoken_day(at: datetime) -> str:
    """`26th Oct` - the toolkit's own spelling of a day, in whatever zone `at` is already
    in.

    ONE spelling, because three surfaces name the same day and a student who is told one
    date by the Join-team form and another by the mail beside it has been told two things.
    The team-formation mail, the semester site's team-formation callout and the form's
    refusal all come through here; the form's own JavaScript carries its copy
    (`spokenDate`) because it cannot import this one. The gradebook's Submitted column does
    NOT (`_submitted_display`): respelling it would rewrite every student's grades.yml."""
    day = at.day
    suffix = (
        "th"
        if 11 <= day % 100 <= 13
        else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )
    return f"{day}{suffix} {_MONTHS[at.month - 1]}"


_PRIVACY_HEADER = (
    "This gradebook is private to you. It is regenerated each time grades are "
    "distributed; do not edit it."
)
_README_COLUMNS = ("Assignment", "Final grade", "Submitted", "Late", "Team")
# What the Submitted column says where there is no time to show. The two are different
# facts and a student reads them as such: `external` is "we never expected a commit here",
# `not submitted` is "we looked, and nothing was in the repo". Calling the second one
# external told a student who missed a deadline that their assignment was handed in
# somewhere else.
_EXTERNAL = "external"
_NOT_SUBMITTED = "not submitted"
_REGISTRAR_FIELDS = ("hertie_email", "name", "github_handle")


def _marked(score: object) -> dict:
    """A per-question score with its unmarked questions dropped ({} for a flat score).

    A blank question is one nobody has marked yet, and `Q3: null` in a student's file
    reads as a mark of nothing rather than as no mark at all."""
    if not isinstance(score, dict):
        return {}
    return {name: value for name, value in score.items() if not is_blank(value)}


def _verbatim(score: object) -> str:
    """A score no arithmetic applies to, exactly as typed - `pass`, `A-`, `see me`.

    Only a flat cell has one: a per-question map with a word in it has no single value to
    pass through, and the map itself is already in the view."""
    return "" if isinstance(score, dict) or is_blank(score) else str(score).strip()


def _percent_off(fraction: Decimal) -> str:
    """`-20%`, rounded to two decimals, because `rate` is a hundredth of whatever the course
    wrote and the product carries its trailing digits: `3.333%` for three days is a
    deduction, not `-9.999%`."""
    return f"-{_plain((fraction * 100).quantize(Decimal('0.01')))}%"


def _penalty_display(
    rate: Decimal | None, days_late: object, late_penalty: object = None
) -> str:
    """What the late days cost, in the words the student is told them in: `-20%` as the
    late rule computes it, `waived` (a set `0%` too), or `-10% (set by hand)`
    (`effective_penalty`). Empty for a unit that is not late. A `late_penalty` nobody can
    read shows the late rule's figure, because that is what applies as things stand: the
    unit's mark is held (`sheet_hold_reasons`). A receipt never passes `late_penalty`
    (`penalty_display`): the grader's choice reaches a student only with their marks."""
    days = as_decimal(days_late)
    if days is None or days <= 0:
        return ""
    penalty = effective_penalty(rate, days, late_penalty)
    if penalty.mode == "waive":
        return "waived"
    if penalty.mode == "set":
        return f"{_percent_off(penalty.fraction)} (set by hand)"
    return _percent_off(penalty.fraction) if rate is not None else ""


def _submitted_display(value: object, external: bool = False) -> str:
    """A recorded submission time as a person reads it: `3 Oct 22:14`.

    `external` - the assignment is handed in off GitHub - says so instead, and is the ONLY
    thing that may: there is no commit to time, so a blank here is the shape of the
    assignment rather than a missing submission. An `info:` block with no `submitted` in
    it is the other case, and comes back "" for the caller to name.

    Anything that does not parse as a timestamp comes back verbatim. `info:` is the
    toolkit's, but a grader may have typed over it, and their words about their own
    semester beat this module's guess at what they meant."""
    if external:
        return _EXTERNAL
    if is_blank(value):
        return ""
    text = value.isoformat() if isinstance(value, datetime) else str(value).strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    day = f"{moment.day} {_MONTHS[moment.month - 1]}"
    return day if "T" not in text and " " not in text else f"{day} {moment:%H:%M}"


def question_feedback(spec: SheetSpec, value: object) -> dict[str, str]:
    """The per-question feedback worth sending: the filled cells, in the order the
    assignment declares its questions, then any other question as the grader typed it
    (a mistyped name still reaches the student under the name it was given)."""
    if not isinstance(value, dict):
        return {}
    said = {
        str(name): str(text).strip()
        for name, text in value.items()
        if not is_blank(text) and not isinstance(text, dict)
    }
    declared = [name for name in (spec.questions or {}) if name in said]
    return {
        name: said[name] for name in [*declared, *sorted(set(said) - set(declared))]
    }


def _allowlisted(fields: dict) -> dict:
    """`fields` reduced to the student-visible keys, in STUDENT_VIEW_KEYS order, blanks
    dropped. Every student-facing value in this module passes through here."""
    return {
        key: fields[key]
        for key in STUDENT_VIEW_KEYS
        if key in fields and not is_blank(fields[key])
    }


def student_view(
    spec: SheetSpec, unit_key: str, block: dict | None, handle: str
) -> dict:
    """What ONE student may be shown of one submission unit's grading-sheet entry.

    `block` is that unit's entry: `submissions[handle]` on an individual assignment, where
    `unit_key` IS the handle; `teams[team]` on a group one, where `unit_key` is the team
    and `handle` picks the member whose view this is. Nothing of any other member is read,
    and nothing outside STUDENT_VIEW_KEYS is emitted, so the two ways a gradebook leaks -
    the grader's private notes, and a team-mate's marks - are both closed structurally.

    The final grade is DERIVED here (the score total, the late penalty, then this member's
    own adjustment) because the sheet has no field for it. A score no arithmetic applies
    to - `pass`, `A-` - is passed through exactly as typed, and `needs_hand_decision`
    flags it where a penalty would have applied to a number."""
    block = block or {}
    info = block.get(INFO_KEY) or {}
    person = _people(spec, unit_key, block).get(handle) if spec.is_group else block
    person = person if isinstance(person, dict) else {}
    score = block.get(spec.score_key)
    days_late = info.get("days_late")
    rate = penalty_rate(spec.late_penalty_per_day)
    late_penalty = block.get(LATE_PENALTY_KEY)
    final = final_grade(
        score_total(score, spec.questions),
        rate,
        days_late,
        person.get("adjustment_individual"),
        late_penalty,
    )
    return _allowlisted(
        {
            "final_grade": _plain(final) if final is not None else _verbatim(score),
            # A group's score is the TEAM's: it reaches the team in the team's own repo,
            # and reaches the member only through the final grade derived from it. Unless
            # there IS no team repo - a shape that creates none makes the gradebook the
            # only place that score can be read at all, and withholding it there would
            # leave a team with a grade nobody was ever shown the marks behind.
            # `STUDENT_VIEW_KEYS` is untouched: the allowlist stays the guarantee.
            "score": (
                None
                if (spec.is_group and spec.creates_unit_repos)
                else (_marked(score) or score)
            ),
            "max_points": total_points(spec),
            "feedback": person.get("feedback_individual"),
            QUESTION_FEEDBACK_KEY: question_feedback(
                spec, block.get(QUESTION_FEEDBACK_KEY)
            ),
            "submitted": _submitted_display(
                info.get("submitted"), not spec.collects_commits
            ),
            "days_late": days_late,
            "penalty": _penalty_display(rate, days_late, late_penalty),
            "team": unit_key if spec.is_group else None,
            "team_feedback": block.get("feedback_group") if spec.is_group else None,
        }
    )


def needs_hand_decision(spec: SheetSpec, block: dict) -> bool:
    """Whether this unit's mark needs a person to settle it: a score that is not a number,
    under a late penalty that takes something off (`effective_penalty`).

    `pass`, two days late, is not `-20% of pass`. The distribute dry run counts these so
    that a grader decides them before anything is sent, rather than after a student reads
    it.

    A row with NOTHING typed in it yet is not one of them - there is no decision to take
    about a mark nobody has written - nor is one whose penalty is waived or nothing: it
    takes nothing off, so there is nothing to decide."""
    score = block.get(spec.score_key)
    if score_total(score, spec.questions) is not None or not _verbatim(score):
        return False
    info = block.get(INFO_KEY) or {}
    penalty = effective_penalty(
        penalty_rate(spec.late_penalty_per_day),
        info.get("days_late"),
        block.get(LATE_PENALTY_KEY),
    )
    return penalty.fraction > 0


def undecided_marks(spec: SheetSpec, sheet: dict) -> dict[str, str]:
    """`{handle: "penalty"}` for everyone in this sheet whose mark needs a person to settle
    it (`needs_hand_decision`) - the shape `sheet_hold_reasons` gives."""
    return {
        handle: "penalty"
        for unit_key, block in _units(spec, sheet)
        if needs_hand_decision(spec, block)
        for handle in _people(spec, unit_key, block)
    }


def _views_from_sheet(spec: SheetSpec, sheet: dict) -> dict[str, dict]:
    """Every student's view of one grading sheet, keyed by handle."""
    views: dict[str, dict] = {}
    for unit_key, block in _units(spec, sheet):
        for handle in _people(spec, unit_key, block):
            views[handle] = student_view(spec, unit_key, block, handle)
    return views


def build_gradebooks(
    sources: dict[str, tuple[SheetSpec, dict]],
) -> dict[str, dict[str, dict]]:
    """Pivot every assignment's grading sheet into `{handle: {slug: view}}` - one book per
    student, one entry per assignment they have a mark or a word of feedback on.

    Assignments are folded in sorted order, so a re-run renders byte-identical files and
    only a gradebook that really changed is committed and emailed. An empty view is not an
    entry: a student with nothing in the sheet yet has nothing to be told."""
    books: dict[str, dict[str, dict]] = {}
    canonical: dict[str, str] = {}  # fold key -> the first spelling seen for it
    for slug in sorted(sources):
        for handle, view in _views_from_sheet(*sources[slug]).items():
            if not view:
                continue
            key = canonical.setdefault(handle.casefold(), handle)
            books.setdefault(key, {})[slug] = view
    return books


def _on_the_roster(
    books: dict[str, dict[str, dict]], students: list[roster.Student] | None
) -> tuple[dict[str, dict[str, dict]], int]:
    """The books belonging to somebody this semester's roster knows, and how many marks the
    rest accounted for.

    A grading sheet is hand-typed, so it carries blocks for handles the semester does not
    have: a student who withdrew before onboarding, a handle typed from memory, a sheet
    carried over wholesale from the term before. `ensure_gradebooks`
    provisions one repo per ONBOARDED enrolled student and nothing else, so a book for any
    other handle was a write to a repo that does not exist - a 404 per row, in a PUBLIC
    log, naming `grades-<handle>` as it went. Dropping them here is what makes the two
    agree.

    A roster that could not be READ (None) filters nothing: the run is already going red
    for it, and treating an unreadable file as "nobody is enrolled" would withhold the
    whole semester's grades on the strength of a transient failure."""
    if students is None:
        return books, 0
    known = {
        s.github_handle.casefold() for s in roster.enrolled(students) if s.onboarded
    }
    kept = {
        handle: book for handle, book in books.items() if handle.casefold() in known
    }
    unknown = 0
    for handle in sorted(set(books) - set(kept)):
        unknown += len(books[handle])
        log_person(
            f"  [unknown] {handle} is not an onboarded student on this roster - "
            f"{len(books[handle])} mark(s) ignored"
        )
    return kept, unknown


def _cell(value: object) -> str:
    """One value as a Markdown table cell: no `|` to close the column early, no newline to
    end the row. A grader's feedback is free text and can reach a table either way."""
    text = "" if is_blank(value) else " ".join(str(value).split())
    return text.replace("|", "\\|")


def _over_max(value: object, max_points: object) -> str:
    """`40 / 50` where the assignment declares a total, `40` where it does not."""
    text = "" if is_blank(value) else str(value).strip()
    return f"{text} / {max_points}" if text and not is_blank(max_points) else text


def _late_display(days_late: object) -> str:
    """`on time`, `1 day late`, `2 days late` - "" where nothing was timed."""
    days = as_decimal(days_late)
    if days is None:
        return ""
    if days <= 0:
        return "on time"
    return f"{_plain(days)} day{'' if days == 1 else 's'} late"


def _readme_label(slug: str, title: str) -> str:
    """`Assignment 5 · Portfolio piece`: the identifier the site and schedule show, then the
    name, with a name that merely repeats the identifier folded away (`course.row_name`).
    A book whose slug has no title yet is labelled by the identifier alone."""
    ident = identifier(slug)
    name = row_name(title, ident) if title != slug else ""
    return f"{ident} · {name}" if name else ident


def _readme_row(title: str, view: dict) -> str:
    """One assignment's row in the summary table."""
    values = (
        title,
        _over_max(view.get("final_grade", ""), view.get("max_points")),
        # An external assignment says so; anything else with no time on it is a repo
        # nothing was ever pushed to.
        view.get("submitted") or _NOT_SUBMITTED,
        _late_display(view.get("days_late")),
        view.get("team", ""),
    )
    return "| " + " | ".join(_cell(value) for value in values) + " |"


def _readme_grade_line(view: dict, grade: str) -> str:
    """The final grade, and - where a deadline moved it - the arithmetic that produced it:
    `**Score:** 20 / 50 · 2 days late · penalty -20% · **Final grade:** 16 / 50`.

    A student cannot reconstruct that from the final grade alone, and without it a
    deduction reads as a harsh mark. It used to be the opening line of the feedback comment
    on their own repo; that channel is closed, and the gradebook is the only place any of
    it is written now.

    Written only where there is a separate score AND something timed it. A hand-marked unit
    has a final grade and no score behind it, an `external` assignment counts no days, and
    a team's score is not in a member's view at all (`student_view`) - in each of those the
    final grade is the whole of what there is to say, and `**Score:** 40 · **Final grade:**
    40` would be arithmetic theatre. The helpers are the display ones the table already
    uses, so a row and its section cannot word the same fact differently."""
    total = score_total(view.get("score"))
    late = _late_display(view.get("days_late"))
    if total is None or not late:
        return f"**Final grade:** {grade}"
    facts = [f"**Score:** {_over_max(_plain(total), view.get('max_points'))}", late]
    if view.get("penalty"):
        facts.append(f"penalty {view['penalty']}")
    facts.append(f"**Final grade:** {grade}")
    return _SEP.join(facts)


def _readme_section(title: str, view: dict) -> str:
    """One assignment's section: the final grade with whatever was done to it, the
    student's own feedback verbatim, and the team's feedback as a blockquote that says who
    else has read it."""
    grade = _over_max(view.get("final_grade", ""), view.get("max_points"))
    parts = [f"## {title}" + (f"\n{_readme_grade_line(view, grade)}" if grade else "")]
    if not is_blank(view.get("feedback")):
        parts.append(str(view["feedback"]).strip())
    per_question = "\n".join(
        _question_item(name, text)
        for name, text in (view.get(QUESTION_FEEDBACK_KEY) or {}).items()
    )
    team = view.get("team")
    if not is_blank(view.get("team_feedback")) or (team and per_question):
        # A team's per-question feedback is the team's, so it sits inside the quote that
        # says who else has read it.
        label = f"**Team feedback (shared with {team or 'your team'}):**"
        lines = str(view.get("team_feedback") or "").strip().split("\n")
        quoted = [f"> {label} {lines[0]}".rstrip()]
        quoted += [f"> {line}".rstrip() for line in lines[1:]]
        if team and per_question:
            quoted += [
                ">",
                *(f"> {line}".rstrip() for line in per_question.split("\n")),
            ]
        parts.append("\n".join(quoted))
    if per_question and not team:
        parts.append(per_question)
    return "\n\n".join(parts)


def _undeclared_feedback(spec: SheetSpec, units: dict) -> int:
    """How many units' `feedback_per_question` names a question `spec` does not declare."""
    declared = set(spec.questions or {})
    return sum(
        1
        for block in units.values()
        if isinstance(block, dict)
        and isinstance(said := block.get(QUESTION_FEEDBACK_KEY), dict)
        and any(str(q) not in declared and not is_blank(t) for q, t in said.items())
    )


def _question_item(name: str, text: str) -> str:
    """One question's feedback as a list item, a multi-line text kept inside it."""
    first, *rest = str(text).strip().split("\n")
    return "\n".join([f"- **{name}:** {first}", *(f"  {line}" for line in rest)])


def render_readme(handle: str, book: dict[str, dict], titles: dict[str, str]) -> str:
    """One student's gradebook README - the file they actually open.

    The privacy line, one row per assignment, then a section per assignment with their
    feedback. It gives the final grade and never the sum behind it: the team's score and
    their own adjustment are their grader's working, and a student reading their own
    deduction beside their team-mates' shared mark is exactly the conversation this
    workflow exists to avoid.

    `handle` is the student the book belongs to; the text names nobody - the repo is
    already private to them - and takes it so that every per-student write reads the
    same at the call site. `titles` maps a slug to the assignment's name, falling back to
    the slug rather than rendering an empty heading."""
    del handle
    slugs = sorted(book)
    table = [
        "| " + " | ".join(_README_COLUMNS) + " |",
        "|" + "---|" * len(_README_COLUMNS),
        *(
            _readme_row(_readme_label(slug, titles.get(slug, slug)), book[slug])
            for slug in slugs
        ),
    ]
    sections = [
        _readme_section(_readme_label(slug, titles.get(slug, slug)), book[slug])
        for slug in slugs
    ]
    return "\n\n".join([_PRIVACY_HEADER, "\n".join(table), *sections]) + "\n"


def render_registrar_csv(
    students: list[roster.Student], books: dict[str, dict[str, dict]]
) -> str:
    """The semester grades file: one row per ENROLLED student, one column per assignment,
    each cell the final grade exactly as that student was told it.

    Every enrolled student is a row, marked or not, and a student who has not onboarded
    yet is a row with no handle: a missing row reads as somebody who left the course, and
    this is the file a grade is transcribed from. Auditors are never assessed and are
    never rows. It lives in the private semester-config and is never logged."""
    slugs = sorted({slug for book in books.values() for slug in book})
    by_handle = {handle.casefold(): book for handle, book in books.items()}

    def row(student: roster.Student) -> list[str]:
        book = by_handle.get(student.github_handle.casefold()) or {}
        return [
            student.hertie_email,
            student.name,
            student.github_handle,
            *(str(book.get(slug, {}).get("final_grade", "")) for slug in slugs),
        ]

    return dump_csv(
        [*_REGISTRAR_FIELDS, *slugs],
        (
            row(student)
            for student in sorted(
                roster.enrolled(students), key=lambda s: s.hertie_email.casefold()
            )
        ),
    )


def _tag_gradebook(semester_org: str, repo: str, have: set[str]) -> None:
    """Stamp `gradebook` on one private gradebook repo. Checked.

    Called on the ALREADY-EXISTS path too: the stamp is a separate PUT after the create,
    and one that failed used to stand until `access.converge_topics` came round on the
    nightly refresh. `have` is the repo's topics off the caller's listing, so a gradebook
    already carrying the topic costs no call, and whatever else it carries is written back
    with it (the PUT replaces the whole list).

    A failed PUT is said out loud rather than dropped, because the topic is what the
    faculty-access floor and the release targets read. It is not a leak, though:
    `discovery._has_infra_topic` recognises `grades-<handle>` by NAME whatever its topics
    say, precisely so a failed stamp cannot put it on a public page. A backstop is not a
    reason to leave the record wrong - it is the reason the line below does not cry fire.
    """
    if "gradebook" in have:
        return
    if not set_repo_topics(
        semester_org, repo, sorted(have | {"gradebook"}), person=True
    ):
        log_err(
            f"  ! a gradebook in {semester_org} carries no `gradebook` topic. The name rule "
            f"in `discovery._has_infra_topic` still keeps it off the public org landing "
            f"page, so no handle is published - but the record stays wrong until the "
            f"stamp lands. The next sync with a repo listing retries it, as does the "
            f"nightly refresh."
        )


def provision_one(
    semester_org: str,
    handle: str,
    existing: dict[str, dict] | None = None,
    collaborators: dict[str, dict[str, str]] | None = None,
) -> str:
    """Ensure a private grades-<handle> repo exists with the student as read collaborator.

    `existing` is the semester's repos off ONE listing (`discovery.listing_by_name`), keyed
    by name; membership in it answers "is this gradebook already there?" without a GET per student,
    and each row carries the `topics` that `_tag_gradebook` converges off. None - no
    listing to hand - falls back to probing this one repo, and skips that convergence
    rather than paying a read per student for it."""
    repo = f"{GRADEBOOK_PREFIX}{handle}"
    existed = exists_in(existing, semester_org, repo)
    if existed:
        log_person(f"  [skip] gradebook {semester_org}/{repo}")
        # Converge the stamp off the listing row that already answered "is it there?",
        # rather than pay a read per student for it. An ARCHIVED gradebook is passed over:
        # it is read-only, so the PUT would 403 on every sync, and a finished semester is
        # meant to stay frozen - `access.converge_topics` skips them for the same reason.
        row = existing[repo] if existing is not None else None
        if row is not None and not row.get("archived"):
            _tag_gradebook(semester_org, repo, set(row.get("topics") or []))
    else:
        if not create_repo(
            semester_org,
            repo,
            private=True,
            description=f"Private gradebook for @{handle}",
            person=True,
        ):
            return "failed-create"
        if existing is not None:
            # The caller's listing is now one repo out of date, and in a scheduler tick
            # the passes after this one read it (`discovery.listing_row`).
            existing[repo] = listing_row(semester_org, repo)
        put_file(
            semester_org,
            repo,
            "README.md",
            _STARTER_README.encode(),
            "init gradebook",
            person=True,
        )
        _tag_gradebook(semester_org, repo, set())

        # At creation only: a team grant does not decay, and the nightly sweep
        # (access.converge_faculty_access) owns the floor for every gradebook that already
        # exists - so re-granting on every sync cost two PUTs per student for nothing.
        #
        # Read, not write: `distribute` rewrites grades.yml from the grading sheet, so a
        # mark corrected here would be overwritten on the next run. The sheet is where a
        # mark belongs.
        grant_faculty(
            semester_org,
            repo,
            FACULTY_READ_ACCESS,
            missing_is_note=True,
            person=True,
        )
    held = (collaborators or {}).get(repo.casefold()) or {}
    if existed and held.get(handle.casefold()) == "READ":
        # Already the gradebook's read collaborator (`repos.direct_collaborators_by_repo`):
        # the PUT would change nothing, and the nightly sync used to make it per student.
        # Held at anything else - more than read included - the PUT puts it back to read.
        log_person(f"  [ok]   + @{handle} (read)")
        return "skipped"
    if add_collaborator(
        semester_org, repo, handle, permission=GRADEBOOK_PERMISSION, person=True
    ):
        log_person(f"  [ok]   + @{handle} (read)")
        return "skipped" if existed else "ok"
    # A gradebook the student can't open is a failure, not a partial success - the status
    # starts with "failed" so it reaches the exit code (see sync).
    log_err(f"  ! could not add @{handle} (not a real account?)")
    return "failed-no-collaborator"


def ensure_gradebooks(
    semester_org: str,
    dry_run: bool = False,
    existing: dict[str, dict] | None = None,
    budget_minutes: float | None = None,
    collaborators: dict[str, dict[str, str]] | None = None,
) -> int:
    """Provision one private gradebook repo per onboarded enrolled student. Idempotent.

    Named for what it does, and called by `distribute` before anything is written into
    one: a student who onboarded since the last run has no repo to push a grade into, and
    the failure a moment later would say only "could not write".

    Auditors are read-only and are never assessed, so they get no gradebook.

    `existing` is the semester's repos off a listing the CALLER already holds, keyed by name;
    None means take one here. Every caller but the nightly sync has just listed the org for
    its own reasons, and a second listing per release run answers the same question twice.

    `budget_minutes` bounds the WALL CLOCK: once it is spent this stops and says how many
    students are left, and they wait for the next run. None - the default - is unbounded,
    which is what a caller with work waiting on these repos needs: a handout that stopped
    halfway would publish a brief pointing at gradebooks half the semester does not have,
    and `distribute` is about to write a mark into every one of them. Only the nightly
    `sync_membership` passes a budget, because nothing in that run waits on the result -
    and the number of minutes is that caller's own
    (`sync_membership.GRADEBOOK_BUDGET_MINUTES`).

    A roster that is absent or empty is a SKIP, not a failure, for the reason
    `sync_roster.sync` gives: an empty roster is a freshly bootstrapped semester and a
    missing one is a content fault the roster's own digest issue already reports to the
    people who can fix it. This runs on every nightly Sync membership now, and reddening
    that run would tell a maintainer only that something is wrong in an org they cannot fix
    it in. `distribute` has its own guard: it is about to write a mark per student, so an
    unreadable roster stops it."""
    students = roster.load(semester_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 0
    if not students:
        log(f"  [skip] no gradebooks in {semester_org} - its roster has no rows yet")
        return 0
    participants = roster.enrolled(students)
    auditing = len(students) - len(participants)
    onboarded = [s for s in participants if s.onboarded]
    skipped = len(participants) - len(onboarded)
    log_step(f"Syncing {len(onboarded)} gradebook repo(s) in {semester_org}")
    if skipped:
        log(f"  ({skipped} not-yet-onboarded row(s) skipped)")
    if auditing:
        log(f"  ({auditing} auditor row(s) skipped - read-only, never assessed)")

    # ONE listing of the semester answers "is it already there?" for every student below.
    # A dry run creates nothing, so it needs no answer.
    if existing is None and not dry_run:
        existing = listing_by_name(semester_org)
    results: dict[str, int] = {}
    deferred = 0
    started = time.monotonic()
    for done, s in enumerate(onboarded):
        if dry_run:
            log_person(
                f"    PREVIEW  {semester_org}/{GRADEBOOK_PREFIX}{s.github_handle}"
            )
            continue
        if budget_minutes is not None and (
            time.monotonic() - started > budget_minutes * 60
        ):
            # STOP rather than step through what is left: the budget is spent, so every
            # student after this one is deferred by the same clock.
            deferred = len(onboarded) - done
            break
        status = provision_one(semester_org, s.github_handle, existing, collaborators)
        results[status] = results.get(status, 0) + 1
    if dry_run:
        return 0
    if deferred:
        # A COUNT, and green: nothing is lost, and the students who do have one were all
        # reconciled. `ok` is `provision_one`'s word for a gradebook it CREATED, so the
        # line says what the budget actually bought. The next run takes the next batch.
        log(
            f"  ({deferred} more gradebook(s) on the next run - {results.get('ok', 0)} "
            f"created here, and one run spends at most {budget_minutes:g} minute(s) on it)"
        )
    log_ok(f"Done - {json.dumps(results)}")
    return 1 if any(k.startswith("failed") for k in results) else 0


# ---------------------------------------------------------------- what was distributed

# One row per thing SAID, so a re-run says nothing twice and a failure is retried exactly
# once. It replaces `gradebook/notified.csv`, which recorded only the email and only per
# student - so a corrected grade re-emailed the whole semester, and a write that failed was
# never retried because nothing recorded that it had not.
DISTRIBUTED_PATH = records.path("distributed")
DISTRIBUTED_HEADER = (
    "target",  # a handle; a TEAM name on the rows the retired issue channel left behind
    "assignment",  # the semester-side slug; "" for the whole-book email
    "channel",
    "content_hash",
    "distributed_at",
    # The issue a retired `issue` row's comment landed on. Nothing writes it now; the
    # column stays so a file written before marks moved to the gradebook alone still reads
    # and still round-trips, rather than losing a column on its first rewrite.
    "issue",
)
# Marks and feedback go to ONE place, the student's gradebook, so the channels are the
# gradebook, the semester grades file and the email. `issue` is RETIRED: no run writes it,
# and it is named here only so a row left by an older run is recognisable as one.
CHANNEL_ISSUE = "issue"
CHANNEL_GRADEBOOK = "gradebook"
CHANNEL_EMAIL = "email"
# `{(target, assignment, channel): (content hash, when, issue number or "")}`
Distributed = dict[tuple[str, str, str], tuple[str, str, str]]


def content_hash(text: str) -> str:
    """The short hash `distributed.csv` records a gradebook commit under."""
    return blob_sha(text.encode())[:12]


def parse_distributed(text: str) -> Distributed:
    """`distributed.csv` into its lookup. Machine-written, but it sits in a repo faculty
    can edit, so it goes through the same BOM/delimiter guard as the roster."""
    return {
        (
            (row.get("target") or "").strip(),
            (row.get("assignment") or "").strip(),
            (row.get("channel") or "").strip(),
        ): (
            (row.get("content_hash") or "").strip(),
            (row.get("distributed_at") or "").strip(),
            (row.get("issue") or "").strip(),
        )
        for row in read_csv(text, ("target",), DISTRIBUTED_PATH)
        if (row.get("target") or "").strip()
    }


def dump_distributed(records: Distributed) -> str:
    """Sorted, so a run that changed one row shows one line in the diff."""
    return dump_csv(
        DISTRIBUTED_HEADER,
        (
            (target, assignment, channel, digest, when, issue)
            for (target, assignment, channel), (digest, when, issue) in sorted(
                records.items()
            )
        ),
    )


def _read_distributed(wd: Path) -> tuple[Distributed, bool]:
    """`(what has been distributed, whether this run is the migration)`.

    A semester part-way through the term has `notified.csv` and no `distributed.csv`. Its
    rows become EMAIL rows here, so nobody is emailed again for a book they already know
    about - the hash is over different bytes now, so the first run after the migration
    does re-tell everyone once; that is what `--no-notify` is for."""
    live = wd / DISTRIBUTED_PATH
    if live.is_file():
        return parse_distributed(live.read_text()), False
    old = wd / NOTIFIED_PATH
    if not old.is_file():
        return {}, False
    # read_csv, not a bare DictReader: the file is machine-written, but it sits in a repo
    # faculty can edit, so it goes through the same BOM/delimiter guard as the roster.
    return {
        ((row.get("github_handle") or "").strip(), "", CHANNEL_EMAIL): (
            (row.get("grades_sha") or "").strip(),
            (row.get("notified_at") or "").strip(),
            "",
        )
        for row in read_csv(old.read_text(), ("github_handle",), NOTIFIED_PATH)
        if (row.get("github_handle") or "").strip()
    }, True


def _retired_gradebook_files(wd: Path) -> list[str]:
    """The per-student YAML the retired `render` staged for its preview PR. The gradebook
    repos hold the real thing now, and a stale copy of a grade is worse than none.

    Asked on EVERY run, not only on the `notified.csv` migration: a semester that reached
    `distributed.csv` without ever having had a notified.csv - which is every semester
    bootstrapped since - was never on the migration path, so its `gradebook/*.yml` sat
    there for the rest of the term. They are dead either way, and the only file in that
    folder anything still reads is `distributed.csv`, which is not a `.yml`."""
    folder = wd / GRADEBOOK_DIR
    if not folder.is_dir():
        return []
    return sorted(f"{GRADEBOOK_DIR}/{p.name}" for p in folder.glob("*.yml"))


def semester_grades_file(wd: Path) -> Path:
    """The semester grades file in a semester-config checkout: at its name, or - until the
    first real run after 0.11.0 renames it - at `records.RETIRED_SEMESTER_GRADES`."""
    path = wd / SEMESTER_CSV_NAME
    retired = wd / records.RETIRED_SEMESTER_GRADES
    return retired if not path.is_file() and retired.is_file() else path


def _told_grades(wd: Path) -> tuple[dict[str, dict[str, str]], set[str]]:
    """`({handle (casefolded): {slug: final grade}}, the file's columns)` as the last
    real run wrote the semester grades file, so the preview can say which grades a run
    would CHANGE and which columns it would ADD. Empty when there is no file yet or it
    cannot be read, and every grade and column is then new."""
    path = semester_grades_file(wd)
    if not path.is_file():
        return {}, set()
    try:
        reader = read_csv(path.read_text(), ("github_handle",), SEMESTER_CSV_NAME)
        rows = list(reader)
    except RuntimeError:
        return {}, set()
    told = {
        row["github_handle"].strip().casefold(): {
            k: v for k, v in row.items() if k and v and k not in _REGISTRAR_FIELDS
        }
        for row in rows
        if (row.get("github_handle") or "").strip()
    }
    return told, set(reader.fieldnames or ())


def _already_returned(wd: Path) -> set[str] | None:
    """The assignments students have already been told a mark for: each with at least one
    non-empty cell in the semester grades file the last real run wrote. None when that
    cannot be told - the file is there and cannot be read, or it is missing while
    gradebooks have already been written (`distributed.csv` has rows)."""
    path = semester_grades_file(wd)
    if not path.is_file():
        distributed, _ = _read_distributed(wd)
        return None if distributed else set()
    try:
        rows = list(read_csv(path.read_text(), ("github_handle",), SEMESTER_CSV_NAME))
    except RuntimeError:
        return None
    return {
        slug
        for row in rows
        for slug, value in row.items()
        if slug and slug not in _REGISTRAR_FIELDS and (value or "").strip()
    }
