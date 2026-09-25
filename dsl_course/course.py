"""The course DOMAIN vocabulary - the names and rules that describe a course, with no
GitHub or I/O of its own (stdlib only).

Everything here was previously spelled out two or three times across the modules that
needed it. Declared once, in the layer everything else can import, so a rename or a
reworded rule cannot reach one consumer and miss another.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

from . import records

# The per-org identity/config file, at the root of every org's `.github` repo: a course
# org's declares its name and its faculty SSOT, a semester org's is a pointer back to it.
COURSE_CONFIG = "dsl-course.yml"
# Its keys that went (decision 0009): the org is the repo owner, the name is `course_name`,
# and a semester's timezone and archive grace are the semester's own facts with the
# institution's defaults. Never read: a file still carrying one is NOT_MIGRATED, and
# `migrate` strips them.
RETIRED_COURSE_KEYS = ("org", "org_name", "cohort_defaults", "semester_defaults")
# The private per-semester config repo: roster, teams, schedule, grades, autograde records.
# Every semester org has exactly one, under exactly this name.
CONFIG_REPO = "semester-config"
# Its name before decision 0010. Spelt here and in `migrate` only: the engine never reads a
# repo under it, and `repos.create_repo` refuses to create one - a migrated semester's old
# name is a live redirect (every clone, API read and sent link resolves through it) for as
# long as nothing else takes the name.
OLD_CONFIG_REPO = "classroom-config"
# The semester's public front door: the Join course and Join team issue forms and the two
# workflows that act on them. Its URL is in every enrolment mail (`discovery.join_issue_url`
# builds it); the ones sent before decision 0010 name `welcome`, which GitHub redirects here
# for as long as no repo takes that name again.
JOIN_REPO = "join"
OLD_JOIN_REPO = "welcome"
RETIRED_REPO_NAMES = frozenset({OLD_CONFIG_REPO, OLD_JOIN_REPO})
# The per-student gradebook repo: grades-<handle> (grades.py creates them, discovery reads
# them back). Named here so the reader and the writer cannot drift.
GRADEBOOK_PREFIX = "grades-"
# Topics on an org's `.github` repo that say which TIER the org is (bootstrap_course stamps
# them; list_orgs enumerates orgs by them). The repo listing carries them, so a sweep can
# tell a course org from a semester without another read.
COURSE_HUB_TOPIC = "dsl-course-hub"
SEMESTER_TOPIC = "dsl-semester"
# The semester topic's old spelling (decision 0012). Never read as a tier: an org still
# carrying it is refused as NOT_MIGRATED, so the migration is what moves it.
OLD_SEMESTER_TOPIC = "dsl-cohort"
# How `scaffold_materials` names every materials repo (`course-materials-<tag>`) - the New
# materials repo workflow takes only the tag, so this prefix is guaranteed by the toolkit
# rather than a convention faculty could deviate from. Named here because `seed.refresh`
# has to recognise a materials repo among the code and dataset repos that
# `discover_content_repos` returns alongside it, and a rename reaching only one side would
# silently stop the convergence it gates.
MATERIALS_REPO_PREFIX = "course-materials-"
# Generated faculty-side files, named where every module that has to know about them can
# see it: `scaffold` writes them, `deploy` refuses to release them, `syllabus` builds one.
# Named rather than re-spelled per module, so the exclusion cannot lapse when one is renamed.
# All three live under `.system/` (decision 0010), out of the root faculty edit.
SYLLABUS_SAMPLE_FILE = records.path("syllabus_sample")
SYLLABUS_SESSIONS_FILE = records.path("syllabus_sessions")
MAINTAINING_FILE = records.path("maintaining")
# The faculty-only heading in the materials README that `scaffold` seeds. `deploy` refuses
# to release a README still containing it, so the sentinel is declared ONCE here - the
# writer and the guard both import it, and neither can lapse when the wording is edited.
FACULTY_ONLY_HEADING = "delete this section before releasing the README"
# The branch an assignment template keeps its solution and grading_config.yml on.
SOLUTION_BRANCH = "solution"
# And the folder ON that branch holding the model answer. Two things read it and neither
# owns it: `assign` pushes the folder into every student repo when the solution is
# released, and `derive` reads the same folder to write `main`'s starter from it. One
# spelling, here, or the two would disagree about where a faculty member puts the answer.
SOLUTION_DIR = "solution"

# The two branches the TOOLKIT owns, as opposed to the ones faculty author. A release lands
# on `UPSTREAM_BRANCH` in each semester dest and is merged from there into the branch students
# read, so a semester-side edit survives the next release instead of being copied over
# (`deploy` is its only writer; every reader resolves the default branch). A semester's own
# edits are proposed back to the course repo on `<PROPOSAL_BRANCH_PREFIX><semester-org>`
# (`propagate`, which regenerates it on every run). Named here rather than re-spelled per
# module because `scaffold` has to recognise both to leave them behind when it copies a repo
# forward, and a rename reaching only one side would quietly copy them again.
UPSTREAM_BRANCH = "upstream"
PROPOSAL_BRANCH_PREFIX = "from-"

# The account every graded subprocess runs as - the students' notebooks, their `run.sh`,
# the hidden tests that import their code, the reading-copy export.
#
# It exists because a UID is the boundary, not an environment: on Linux any process may read
# `/proc/<pid>/environ` of another process running as the SAME user (Yama's ptrace_scope
# gates ATTACH, not that read - it is what lets `ps e` show your own processes), and the
# grading process holds the org-owner PAT for the whole leg. Stripping the token from the
# child's environment and keeping every later step off the runner are both bypassed by one
# `grep GH_TOKEN= /proc/*/environ`.
#
# Declared here, in the shared vocabulary, because two layers spell it and neither owns it:
# `workflows_render` puts the `useradd` in the preamble of every job that grades, and
# `collect` execs each graded command through `sudo -n -u` this name and kills whatever it
# left behind afterwards. One spelling, or the sandbox is silently never used.
SANDBOX_USER = "dsl-sandbox"

# ------------------------------------------- the vocabulary an assignment is defined in

# The closed vocabularies of `grading_config.yml`. Here rather than beside the parser
# because three layers spell them: `workflows_render` builds the New assignment dropdowns
# from them, `scaffold` writes the chosen values into the file, and `grades` reads them
# back - and a dropdown offering a word the reader would refuse is a form that lies.
# Named for where the work LANDS. `assignment_repo` = one repo per unit, pushed to;
# `external` = handed in off GitHub (Moodle, Kaggle, in class), so no repo is created at
# all; `shared_dropbox_repo` = ONE private drop box for the whole semester, one folder per
# unit, every student pushing into their own and reading everyone else's. A word is added
# here when the engine can ACT on it, because this same tuple is what the New assignment
# dropdown offers, and a form offering a word the reader would refuse is a form that lies.
SUBMIT_VIA = ("assignment_repo", "shared_dropbox_repo", "external")
# `github` is the LEGACY spelling of `assignment_repo`: live INSTRUCTOR-OWNED
# `grading_config.yml` files in real course orgs carry it, so it is accepted for ever at
# every read boundary (`canonical_submit_via` below) and never written again by any
# generator. `shared` was renamed to `shared_dropbox_repo` before it ever shipped to a
# real org, so it gets no alias - an unrecognised `shared` is simply Dropped.
LEGACY_SUBMIT_VIA = {"github": "assignment_repo"}


def canonical_submit_via(value: object) -> str:
    """A raw `submit_via` read off the outside world, normalised to its canonical spelling.

    The ONE place every read boundary calls before trusting the value - the
    `grading_config.yml` reader, the New assignment workflow's argv/env, the e2e harness -
    so `github` reads as `assignment_repo` everywhere internal code compares against it,
    and no second copy of the alias can drift from this one."""
    text = str(value or "").strip().lower()
    return LEGACY_SUBMIT_VIA.get(text, text)


# THE `shared_dropbox_repo` rationale, written down once so the five places that act on it
# can point here instead of arguing it out again and drifting: a drop box is ONE repo that
# the whole semester reads, and no student can opt out of being in it. Everything that would
# otherwise be written per unit therefore has nowhere private to go - no Submission receipts issue
# at all (`has_receipts_issue`), no model solution (`can_hold_solution`), and no
# `visibility:` to choose (v1 keeps it private, because `public` would publish every
# student's submission on the strength of one instructor's line). It is hand-marked for a
# different reason: the autograder and the grader's reading copy run per UNIT against the
# unit's own repo, and here fifty units share one.
# Who may read a unit's repo. `private` is the default and what every assignment gets until
# an instructor says otherwise; `public` is portfolio work, world-readable from handout;
# `student_choice` starts private and hands the flag to the student, who may publish their
# own work once it has been marked. Same rule as `SUBMIT_VIA`: a word enters this tuple
# when the handout can CREATE it, so `internal` joins if the plan ever buys an Enterprise.
VISIBILITIES = ("private", "public", "student_choice")
ASSIGNMENT_TYPES = ("individual", "group")
# How a group assignment's teams come about. `none` is NOT one of them: it is the answer
# an INDIVIDUAL assignment gives, which is why the Join-team form can refuse a slug
# outright, and it is not a value an instructor ever writes.
SELF_SELECT = "self_select"  # students use the Join-team form in `join`
ASSIGNED = "assigned"  # the teaching team writes teams.csv; the form refuses
TEAM_FORMATIONS = (SELF_SELECT, ASSIGNED)
NO_TEAMS = "none"
# Which starter stubs `New assignment` seeds, and nothing else: grading reads whatever
# is in the repo, and a student may commit anything. The button takes any number of them,
# comma-separated; `none` is the raw-repo answer and the one that stands alone - which is
# why it is named here, beside the vocabulary it belongs to, rather than spelt again in
# each of the three layers that has to recognise it.
NO_STARTER = "none"
# The stand-in the scaffold seeds where a setting has no sensible default but a shape worth
# showing (`submit_url`). Here because two layers have to agree on it: `scaffold` writes it
# into the file and `grades` refuses to act on a line still carrying it, which is what a
# commented example turning into a live one otherwise costs a semester.
SETTING_PLACEHOLDER = "CHANGE-ME"
FORMATS = ("ipynb", "py", "rmd", "qmd", "latex", NO_STARTER)
# The answer New assignment's format, team_formation, submit_via and visibility boxes
# arrive with when nobody touches them: "use the course's `assignment_defaults:`, else the
# toolkit's own". Here because two layers spell it: the rendered form offers it and the
# scaffold's CLI resolves it.
COURSE_DEFAULT_CHOICE = "(course default)"
# The starters an instructor may actually name, `none` being the answer that means none of
# them: the words the New assignment box offers and the ones `scaffold` refuses back to.
STARTER_FORMATS = tuple(f for f in FORMATS if f != NO_STARTER)

# What the console shows for each value above (exported as `labels.json`), so it holds no
# copy of its own. One entry per value, in the constant's order; `help` may be empty.
LABELS = {
    "formats": {
        "ipynb": {"label": "Jupyter notebook", "help": ""},
        "py": {"label": "Python files", "help": ""},
        "rmd": {"label": "R Markdown", "help": ""},
        "qmd": {"label": "Quarto", "help": ""},
        "latex": {"label": "LaTeX", "help": ""},
        NO_STARTER: {"label": "No starter file", "help": ""},
    },
    "submit_via": {
        "assignment_repo": {
            "label": "Their own repo",
            "help": "Private to the student and instructors.",
        },
        "shared_dropbox_repo": {
            "label": "A shared drop box",
            "help": "One repo for the class; each student has a folder.",
        },
        "external": {
            "label": "Elsewhere",
            "help": "Moodle, Kaggle or in class. The repo carries the brief only.",
        },
    },
    "visibility": {
        "private": {"label": "Private", "help": ""},
        "public": {"label": "Public", "help": ""},
        "student_choice": {"label": "Student's choice", "help": ""},
    },
    "team_formation": {
        SELF_SELECT: {
            "label": "Students form their own",
            "help": "On the student site.",
        },
        ASSIGNED: {
            "label": "You assign them",
            "help": "You assign them on the semester's Teams page once hand out is "
            "scheduled.",
        },
    },
}


def visibility_is_students(visibility: str) -> bool:
    """Whether the toolkit does NOT own this repo's visibility - the student does.

    THE question behind every exemption `student_choice` earns: the repo is created
    private and the student is its admin, so what GitHub says about it later is their
    answer and not the file's. The word is spelt HERE and nowhere else, so an exemption
    cannot be written that agrees with this one only by coincidence - and so a second
    student-owned visibility (an Enterprise `internal` the student may flip) widens every
    one of them at once."""
    return visibility == "student_choice"


def github_visibility(visibility: str) -> str:
    """What GITHUB will call a repo the toolkit handed out under this `visibility:`.

    The config's vocabulary and GitHub's are not the same, in one place: `student_choice`
    is a rule about who may flip the flag AFTER the handout, and the repo GitHub is asked
    to make is private like any other. So a listing row - which carries GitHub's word and
    only GitHub's - never says `student_choice`, and nothing that compares a row against
    the file has to know that on its own account."""
    return "public" if visibility == "public" else "private"


def has_receipts_issue(submit_via: str, visibility: str) -> bool:
    """Whether this shape has a Submission receipts issue at all.

    DERIVED from the shape, never configured: the issue lives in the unit's own repo, so it
    exists exactly where there is one that only that unit can read. A shape without one
    simply gets no receipts; every shape's marks and feedback go to the same place, the
    private `grades-<handle>` gradebook."""
    return submit_via == "assignment_repo" and visibility == "private"


def collects_commits(submit_via: str) -> bool:
    """Whether there are commits to freeze, time and grade against the cutoff.

    The gate on every piece of submission arithmetic - the snapshot, the late days, the
    receipts, the `info:` block - named for what it ASKS, which is why
    `shared_dropbox_repo` widened it here and re-opened none of those call sites: work
    pushed into a folder of the drop box is timed, and is late, exactly as work pushed to a
    repo of one's own.

    Extensionally the same set as `creates_repos` today, and by COINCIDENCE: both are
    false for `external` alone. They are different questions, so widening either one means
    looking at the other."""
    return submit_via in ("assignment_repo", "shared_dropbox_repo")


def creates_unit_repos(submit_via: str) -> bool:
    """Whether the handout creates one repo per unit, which is what gives a unit a repo
    NAME to print, link to and grant on.

    Not the same question as `collects_commits`: a shared drop box collects commits into
    one repo for the whole semester, not one per unit."""
    return submit_via == "assignment_repo"


def can_hold_solution(submit_via: str, visibility: str) -> bool:
    """Whether the model answer can be pushed into the repos this assignment hands out.

    ONE predicate for a question two files ask independently: `assign.provision_all` skips
    the push, and the digest faults a `schedule.yml` `solution_datetime:` that would
    therefore pass and release nothing. Asked apart, the two came to disagree - a
    `shared_dropbox_repo` assignment was scheduled a release the handout silently declined.

    It takes a repo of the unit's OWN (`external` has none, and a shared drop box is one
    repo the whole semester reads) AND a repo the toolkit can promise is private (`public`
    publishes the answers to the internet, `student_choice` lets any student publish
    them). Neither can be taken back, which is why this is a refusal and not a warning."""
    return creates_unit_repos(submit_via) and visibility == "private"


def creates_repos(submit_via: str) -> bool:
    """Whether the handout creates ANYTHING for the work to land in - a repo per unit, or
    the one drop box the whole semester pushes into.

    The third question, and the one the two above cannot answer between them: a shared
    assignment makes no repo per unit and still makes a repo, so everything that asks "is
    there something here whose visibility, topics and faculty floor are ours?" asks this.
    False for `external` alone, which creates nothing at all - which makes it, today and
    by coincidence, the same set as `collects_commits`. See the note there."""
    return submit_via != "external"


def submit_shape(submit_via: str, visibility: str) -> str:
    """The ONE word the semester site branches an assignment on.

    `assignment-repo-private`, `assignment-repo-public`, `assignment-repo-student-choice`,
    `external`, `shared-dropbox-repo`. The two axes are orthogonal in the config and are
    not on the page: a template that asked "which `submit_via`, and then which
    `visibility`?" had to be re-opened for every shape that is neither, and each of the
    four places that asked drifted from the others. So the pair is collapsed HERE, beside
    the predicates it is derived from, and the theme carries one `case`.

    A shape that makes no repo per unit names itself and nothing else (`external`,
    `shared_dropbox_repo`) - there is no per-unit repo for a visibility to describe, and
    the parse drops the key there anyway (`shared_dropbox_repo` is private-only in v1).

    Kebab throughout, whatever the config words look like: this is a word a THEME reads,
    and one shape spelt `assignment_repo-student_choice` beside `assignment-repo-public`
    is a `when` arm somebody eventually mistypes."""
    if not creates_unit_repos(submit_via):
        return submit_via.replace("_", "-")
    return f"{submit_via}-{visibility}".replace("_", "-")


# WHO CAN READ the repo a student was just handed. One text per shape and one place for
# it, because two readers need the same words at two different moments: the assignment's
# page on the semester site (`site._assignment_entry` writes it into the front matter, the
# layout prints it under the brief) and the repo's own About line on GitHub, which is what
# a student reads when they open the repo rather than the page (`assign.provision_one`,
# and the drop box). Written apart they drifted, and the About line said nothing at all.
#
# `NB:` opens every one of them: the box is an aside beside the brief, not a step in it.
# Plain `>` rather than `&gt;` - this is a YAML scalar and a repo description, and the one
# consumer that needs markup escapes it where it renders (`| escape`).
#
# EVERY shape that hands out a repo has one, the ordinary private repo included. It was
# left out as the case with nothing unusual to say, and that is the toolkit's reading, not
# a student's: "who else can see this?" is asked of every repo, and a page that answers it
# for three shapes and goes quiet on the fourth is read as an omission rather than as
# reassurance. `external` has none - it hands out no repo for a sentence to be about.
SHAPE_NOTES = {
    "assignment-repo-private": (
        "NB: this repo is private - only you and the instructors can read it."
    ),
    "assignment-repo-public": (
        "NB: this repo is public, anyone on the internet can read it. Push to main as "
        "usual, but commit nothing you would not publish and no data you were told to "
        "keep private."
    ),
    "assignment-repo-student-choice": (
        "NB: this repo is private-by-default; you are its admin - after the late "
        "cutoff you may make it public from Settings > Danger zone if you want it in "
        "your portfolio."
    ),
    "shared-dropbox-repo": (
        "NB: everyone in the semester can read the whole repo, so commit nothing you "
        "would not show the class."
    ),
}
# When the work is read, said wherever the work's address is given: the route callout on
# the assignment's page (`site._assignment_entry`, then the layout) and the About line of
# every submission repo (`assign._about`). ONE constant, because the two are read minutes
# apart by the same student, and a cutoff worded twice is a cutoff with two answers.
# The manual hand out's solution switch (decision 0012): `solution_datetime`, the schedule's
# own key, where the one value a press can act on is SOLUTION_NOW. Every surface that offers
# it carries SOLUTION_WARNING, because what it does cannot be taken back.
SOLUTION_NOW = "now"
SOLUTION_WARNING = (
    "Pushes the model answer and rubric into every student's repo. This is not returning "
    "marks, and cannot be undone for reuse."
)
CUTOFF_SENTENCE = "What is on main at the late cutoff is what is marked."
# GitHub's cap on a repo description. The About line is `<slug> - submission repo. ` plus
# the cutoff sentence plus the note, so a note that grew past this would be TRUNCATED by
# GitHub rather than refused, and the warning would lose its second half silently.
MAX_REPO_DESCRIPTION = 350


def shape_note(shape: str) -> str:
    """The one-line warning this `submit_shape` owes a student, or "" for a shape that
    owes none."""
    return SHAPE_NOTES.get(shape, "")


# The two answers the New materials repo form asks about PUBLISHING, out of which
# `scaffold.publish_patterns` writes the repo's seeded `publish.yml`. Here, in the shared
# vocabulary, for the same reason the assignment words are: `workflows_render` (layer 3)
# builds the dropdowns and `scaffold` (layer 5) reads the answers back, and a dropdown
# offering a word the reader would refuse is a form that lies.
#
# `(nothing public)` is the default and is spelt with brackets so it cannot be mistaken
# for a directory name - the same device `NO_STARTER`'s neighbours use on their own forms.
NOTHING_PUBLIC = "(nothing public)"
PUBLIC_LECTURES = "lectures"
PUBLIC_EXCEPT_READINGS = "everything except readings"
PUBLIC_EVERYTHING = "everything"
PUBLIC_DIRS = (
    NOTHING_PUBLIC,
    PUBLIC_LECTURES,
    PUBLIC_EXCEPT_READINGS,
    PUBLIC_EVERYTHING,
)
# The file those answers are written into, at the root of a materials repo. A filename
# faculty type by hand, so it is spelt once: `scaffold` seeds it and `site` reads it.
PUBLISH_FILE = "publish.yml"
PUBLIC_HTML = "html"
PUBLIC_HTML_PDF = "html + pdf"
PUBLIC_ALL_FILES = "all files"
PUBLIC_TYPES = (PUBLIC_HTML, PUBLIC_HTML_PDF, PUBLIC_ALL_FILES)


# The four ROLE teams every org's access is expressed in: the two faculty teams, created
# in course and semester orgs alike, and the two semester-only student teams. Named here
# because the grants (access), the reconciles (sync_faculty, sync_roster), the bootstrap
# that creates them and the slugs the student-written Join-team form may never claim
# (sync_teams) all address them by these exact strings.
INSTRUCTORS_TEAM = "instructors"
COURSE_ADMIN_TEAM = "course-admin"
STUDENTS_TEAM = "students"
AUDITORS_TEAM = "auditors"

# Each role team as `(slug, description, privacy)` - what bootstrap creates and what the
# nightly refresh converges a semester's existing teams back to. One table, because a
# privacy asserted only at creation is a privacy every org bootstrapped before the
# decision never gets.
#
# Faculty teams are created in EVERY org (course + semester): instructors run the workflows
# and push content (write); course-admin manage the org (admin).
FACULTY_TEAMS = (
    (INSTRUCTORS_TEAM, "Instructors and TAs", "closed"),
    (COURSE_ADMIN_TEAM, "Course administrators - DSL team", "closed"),
)
# Semester-only role teams: enrolled students + read-only auditors. The persistent course org
# never gets these - it holds unreleased materials, model solutions, and hidden tests, so
# students/auditors must not be near it. Auditors are read-only: assignment release is
# roster-driven (onboarded students only), so auditors never receive assignment repos.
#
# `secret`, not `closed`: a closed team's membership is visible to every member of the org,
# so any student could open the `auditors` team page and read off exactly who is auditing
# rather than enrolled - a classmate's academic status, published to the class by the
# scaffolding. A secret team is visible only to its own members and to org owners, which
# costs the students nothing (nobody needs to browse the roster to do the course).
SEMESTER_TEAMS = (
    (STUDENTS_TEAM, "Enrolled students", "secret"),
    (
        AUDITORS_TEAM,
        "Auditors - read-only (released materials only, no assignments)",
        "secret",
    ),
)
ROLE_TEAMS = frozenset(slug for slug, _, _ in (*FACULTY_TEAMS, *SEMESTER_TEAMS))


# ------------------------------------------------------------------ the Submission receipts issue

# Every submission repo carries ONE issue, opened at handout, where the student's
# submission receipts appear. Marks and feedback are not posted here and never reach a
# repo at all - they go to the student's private gradebook, which is the one address a
# student has to know. The contract lives here, at layer 0, because `assign` opens the
# issue and `collect` posts into it, and the two must agree on the spelling or the second
# one opens a duplicate.
RECEIPTS_ISSUE_TITLE = "Submission receipts"
# The label a new issue is opened with. The lookup is label, then mark, then title - the
# title is the weakest rung, which is what lets it be renamed at all.
RECEIPTS_ISSUE_LABEL = "dsl-receipts"
# Every label an issue has ever been opened under, the written one first. A CHAIN, like
# `RECEIPTS_ISSUE_MARKS` below: these are what the lookup MATCHES against live issues, so a
# label is added, never removed - dropping `dsl-feedback` would make every thread opened
# before the rename invisible, and a second one would appear over it.
RECEIPTS_ISSUE_LABELS = (RECEIPTS_ISSUE_LABEL, "dsl-feedback")
# A tuple, like `gh_contents.STUB_MARKS`: an issue opened under an older wording must still
# be RECOGNISED, so a mark is added to the chain, never edited. Recognition is what stops a
# second Submission receipts issue appearing in a repo that already has one. The first is written.
RECEIPTS_ISSUE_MARKS = (
    "<!-- dsl-course: receipts -->",
    "<!-- dsl-course: feedback -->",
)

_SUBMIT_PARAGRAPH = (
    "Push your work to this repository as normal; the last commit to `main` before the "
    "deadline is what we grade. This thread is your receipt for that: one at the deadline "
    "saying what was recorded, one after any late push, and one at the late cutoff when the "
    "commit we grade is fixed."
)
_CONTRIBUTIONS_ASK = "fill in CONTRIBUTIONS.md before the deadline."

# The three receipt events. One comment each, additive: a comment edited in place leaves no
# trace of when the work actually arrived, and the volume is bounded (one at the deadline,
# one per late push, one at the cutoff).
RECEIPT_DUE = "due"
RECEIPT_UPDATED = "updated"
RECEIPT_FROZEN = "frozen"


def receipts_issue_body(
    *,
    due_display: str,
    late_policy_line: str = "",
    team_line: str = "",
) -> str:
    """The body of a submission repo's Submission receipts issue - what the thread is FOR.

    It says where the work goes and what will be posted here, and nothing about marks: a
    student has one address for those, their private gradebook, and a repo they may be
    told to publish is not a second one.

    The caller supplies only what it knows - the rendered dates, and (for a team) the team
    and its members; every word of boilerplate is here, so the two variants cannot drift
    apart in two call sites.

    There is no variant for an assignment handed in off GitHub: a Submission receipts issue exists
    only where the shape HAS one (`has_receipts_issue`), and no run ever opens one for any
    other shape (`grades.receipts_thread_policy`), so a body describing one would be words
    nobody could reach."""
    lines = [RECEIPTS_ISSUE_MARKS[0], f"**Due:** {due_display}"]
    if late_policy_line:
        lines.append(f"**Late work:** {late_policy_line}")
    if team_line:
        lines.append(f"**Team:** {team_line} - {_CONTRIBUTIONS_ASK}")
    return "\n".join([*lines, "", _SUBMIT_PARAGRAPH]) + "\n"


def receipt_marker(sha: str, event: str) -> str:
    """The hidden marker every receipt carries.

    Keyed on the COMMIT as well as the event, so a re-run that finds the same pin posts
    nothing while a genuinely new push still gets its own receipt. A run with no submission
    to point at keys on `none`, which is equally once-only."""
    return f"<!-- dsl-receipt:{sha or 'none'}:{event} -->"


# What `Distribute grades --receipt-note` posts on a unit's Submission receipts issue, and the hidden
# mark that makes a re-run post it once per assignment. Deliberately NOT a `dsl-receipt:`
# mark: a receipt records a submission, and this records nothing about one.
MARKS_RETURNED_NOTE = "Marks returned: see your marks repo."


def marks_returned_marker(slug: str) -> str:
    """The hidden mark on the marks-returned note for assignment `slug`."""
    return f"<!-- dsl-marks-returned:{slug} -->"


def late_rule(window_days: int | None, penalty: str | None) -> str:
    """The late-work rule an assignment declares, as the half-sentence that follows
    "Late work: " - `10% per day, up to 10 days`, `accepted up to 7 days late`, or `not
    accepted after the deadline`.

    Takes the spec's RESOLVED values (`settings`: the institution's rule for an
    assignment nobody set one for); a window of 0 or None here is a layer that turned late
    work off, not one that has yet to choose.

    The rule itself, spelt once. It is the deadline half of an assignment's page on the
    semester site, and a second spelling of it elsewhere is how one semester comes to read two
    different rules for one deadline.

    NOT `grades.late_policy`, which answers a different question: that is this rule against
    a real cutoff DATE (`accepted until Sunday 11 October 2026, 23:59 ...`), which only an
    assignment with a due date on record can be told."""
    if not window_days:
        return "not accepted after the deadline"
    if penalty:
        return f"{penalty} per day, up to {window_days} days"
    return f"accepted up to {window_days} days late"


def _late_phrase(days_late: int, penalty_display: str = "") -> str:
    """`on time`, `2 days late`, or `2 days late (-20%)`."""
    if days_late <= 0:
        return "on time"
    phrase = f"{days_late} day{'' if days_late == 1 else 's'} late"
    return f"{phrase} ({penalty_display})" if penalty_display else phrase


def receipt_body(
    event: str,
    *,
    sha: str = "",
    pushed_display: str = "",
    days_late: int = 0,
    penalty_display: str = "",
    late_line: str = "",
) -> str:
    """One receipt, as the student reads it.

    "committed", not "pushed": the moment shown is the pinned commit's COMMITTER date,
    which is `GIT_COMMITTER_DATE` and so the student's own to set. Calling it the push time
    said the server had timed it, which it had not - and the pin, the late arithmetic and
    this line all rest on the same claim.

    `late_line` is the course's late policy as a sentence; the caller composes it because
    the dates and the rate come from two other files. Everything else is fixed here."""
    short = sha[:7]
    late = _late_phrase(days_late, penalty_display)
    if event == RECEIPT_FROZEN:
        # No percentage here: the deduction was quoted when the late push was recorded,
        # and this receipt is about the pin closing, not about the mark.
        plain = _late_phrase(days_late)
        return (
            f"**Frozen for grading** · `{short}` · {plain}. No further pushes count.\n"
        )
    if event == RECEIPT_UPDATED:
        return f"**Submission updated** · `{short}` · committed {pushed_display} · {late}\n"
    if not sha:
        opening = "**No submission recorded** at the deadline."
        tail = f" {late_line}" if late_line else ""
        return f"{opening}{tail}\n"
    first = f"**Submission recorded** · `{short}` · committed {pushed_display} · {late}"
    if not late_line:
        return f"{first}\n"
    return f"{first}\n{late_line} A further push replaces this.\n"


def course_phrase(course_name: str) -> str:
    """How student-facing prose names the course inside a sentence: "the X course", or
    plain "the course" when the name could not be resolved.

    One spelling, imported by both student emails (the enrolment code and the grades
    notification), because they land in one inbox and must not name the same course two
    ways. The article and the noun are supplied HERE, not glued on at each call site: a
    display name is a title, not a noun phrase, so a caller that adds only the article
    writes "your grades for the Deep Learning (Demo)". And a course carrying no name yet
    must still read as ordinary English, never as a blank or a literal placeholder."""
    return f"the {course_name} course" if course_name else "the course"


def submission_repo(slug: str, suffix: str) -> str:
    """A submission repo's name: `<slug>-<handle>` individually, `<slug>-<team>` for a
    group. One composition, so the provisioner, the grader and the "your repo is called"
    line on the site cannot spell it differently."""
    return f"{slug}-{suffix}"


def shared_repo(slug: str) -> str:
    """The ONE repo a `submit_via: shared_dropbox_repo` assignment hands out:
    `<slug>-submissions`, a private drop box with a folder per unit inside it.

    Never the bare slug - that is the frozen semester TEMPLATE the brief lives in
    (`assign.ensure_semester_template`). The suffix earns two things for free: the name
    derives from the template, so `discovery.classify_repos` reads it as a student repo and
    the faculty READ floor and the public-page exclusion both apply with no new rule; and
    it carries no handle, so it is the one submission-repo name a public workflow log may
    print in full."""
    return f"{slug}-submissions"


def submission_suffix(repo: str, template: str) -> str:
    """The handle-or-team half of `submission_repo` - what is left once the template the
    repo was generated from is taken off the front."""
    return repo[len(template) + 1 :]


def semester_of(name: str) -> str | None:
    """The fYYYY / sYYYY term tag in an org or repo name (`course-materials-F2026` ->
    'f2026'), or None. Case-insensitive and lowercased, so the same name cannot yield a tag
    on one code path and nothing on another - which two of the three copies of this regex
    did before they were folded into it."""
    m = re.search(r"[fs]\d{4}", name.lower())
    return m.group(0) if m else None


def semester_label(tag: str | None) -> str | None:
    """`f2026` -> `Fall 2026`, `s2027` -> `Spring 2027`: how a semester is displayed."""
    if not tag:
        return None
    return f"{'Fall' if tag[0] == 'f' else 'Spring'} {tag[1:]}"


def pages_repo(org: str) -> str:
    """The GitHub Pages org site repo for an org - pushing it redeploys the site.

    Named `<org>.github.io` so it serves at the org root; `scaffold` creates it under this
    name and `site` syncs it, so the two cannot spell it differently."""
    return f"{org.lower()}.github.io"


def assignment_slug(template: str) -> str:
    """assignment-1-f2026 -> assignment-1 (drop a trailing semester suffix)."""
    return re.sub(r"-[fs]\d{4}$", "", template)


def coerce_date(value: object) -> date | None:
    """A YAML date/datetime or an ISO `YYYY-MM-DD` string -> a `date` (None if unparseable).
    Date-level only (whole-day). The single canonical date coercion: `active_today` here and
    `schedule._coerce_date` both use it, so the two can never drift. An unquoted
    `start: 2026-09-01` in YAML parses to a `datetime.date` (or `datetime`), not a string;
    a quoted one is a string - both land on the same `date`."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):  # date and its datetime subclass both land here
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


# A semester's instructors file (decision 0012): ONE `instructors:` list, each entry with
# a required `role:`. `OLD_PEOPLE_FILE` - a `people:` mapping of role -> list - is never
# read: a semester that still has it is refused as NOT_MIGRATED (`sync_faculty`).
INSTRUCTORS_FILE = "instructors.yml"
# The semester layer of the assignment settings (decision 0010: a `defaults:` block and an
# `assignments:` map of per-slug deviations), at the root of semester-config beside the
# schedule. Named now so the console has no literal; the engine reads it from B2.
ASSIGNMENTS_FILE = "assignments.yml"
OLD_PEOPLE_FILE = "people.yml"
# `role:` value -> the role key every consumer groups by (the old file's own keys).
INSTRUCTOR_ROLES = {
    "instructor": "instructors",
    "teaching_assistant": "teaching_assistants",
}


def people_by_role(meta: object, *, semester: bool = False) -> dict | None:
    """A people block as `{role key: [entries]}`: a semester's `instructors:` list grouped
    by each entry's `role:` (an entry without a valid one is left out - `sync_faculty`
    reports it), or a COURSE file's `people:` mapping as it stands. None when `meta`
    carries neither. A semester's file is read for its list alone: the old `people:`
    shape there is NOT_MIGRATED, and renders no cards."""
    if not isinstance(meta, dict):
        return None
    listed = meta.get("instructors")
    if isinstance(listed, list):
        grouped: dict[str, list] = {key: [] for key in INSTRUCTOR_ROLES.values()}
        for entry in listed:
            role = INSTRUCTOR_ROLES.get(
                str(entry.get("role") or "") if isinstance(entry, dict) else ""
            )
            if role:
                grouped[role].append(entry)
        return grouped
    if semester:
        return None
    people = meta.get("people")
    return people if isinstance(people, dict) else None


def active_today(start: str | date | None, end: str | date | None, today: str) -> bool:
    """Whether `today` (ISO date string) falls within [start, end], either bound optional
    (open-ended if omitted). Bounds may be ISO strings or `datetime.date` objects (an
    unquoted YAML date); an unparseable bound is treated as absent (open-ended on that side)."""
    today_d = coerce_date(today)
    start_d = coerce_date(start)
    end_d = coerce_date(end)
    if start_d and today_d and today_d < start_d:
        return False
    if end_d and today_d and today_d > end_d:  # noqa: SIM103 - guards mirror the docstring
        return False
    return True


def is_repo_root(path: str) -> bool:
    """Whether a plan's `course_source_path` / `semester_dest_path` names the whole repo.

    `""`, `/` and `.` are all the "release everything" spelling, and faculty write all
    three. Stated once because two readers act on it: `deploy._resolve_within` resolves
    each to the clone root, and `schedule.source_faults` has to skip exactly the same
    spellings - a whole-repo line the validator instead read as "a path that does not
    exist" is a source fault mailed about a release that ships perfectly well.
    """
    return path.strip("/").strip() in ("", ".")


# Session directories are named "<ordinal>_<free text>" (e.g. "00_intro",
# "07_finals-review") - only the leading, zero-padding-tolerant ordinal is meaningful;
# the rest is whatever the course calls it. No "week"/"session" literal is required.
_SESSION_PREFIX_RE = re.compile(r"^0*(\d+)_")


def session_number(name: str) -> int | None:
    """Extract the ordinal prefix from a directory name ('00_intro' -> 0, '07_x' -> 7),
    or None if it doesn't start with digits followed by an underscore."""
    m = _SESSION_PREFIX_RE.match(name)
    return int(m.group(1)) if m else None


def session_dirs(dir_paths: Iterable[str]) -> list[tuple[str, str, int]]:
    """THE session-folder rule, over a flat list of relative directory paths.

    `(parent, folder_name, session_number)` for every ordinal-prefixed directory found
    at depth 1 (`NN_.../` - the repo itself is one section, so parent is "") or depth 2
    (`section/NN_.../` - a named section). Anything deeper, and anything without an
    ordinal prefix, is not a session folder. A `parent` is therefore exactly a
    releasable section.

    One rule, two transports: the local filesystem (discover_sections here, used by
    the public-site builder) and the GitHub trees API (dsl_course.discovery) both feed their
    directory listing through this, so "ordinal-prefixed directory = session folder"
    is defined once.
    """
    found = []
    for path in dir_paths:
        parts = path.split("/")
        if len(parts) > 2:
            continue
        n = session_number(parts[-1])
        if n is None:
            continue
        found.append((parts[0] if len(parts) == 2 else "", parts[-1], n))
    return found


def _local_dir_paths(repo_root: Path) -> list[str]:
    """The relative paths of every directory in `repo_root` down to depth 2 - the
    filesystem transport for session_dirs (the API side fetches a git tree instead)."""
    if not repo_root.is_dir():
        return []
    paths = []
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir():
            continue
        paths.append(child.name)
        paths += [
            f"{child.name}/{grandchild.name}"
            for grandchild in sorted(child.iterdir())
            if grandchild.is_dir()
        ]
    return paths


def find_session_dir(section_dir: Path, session: str) -> Path | None:
    """Find the child of `section_dir` whose ordinal prefix matches `session` exactly
    (session='3' matches '3_x'/'03_x'/'003_x', but not '13_x' or '30_x')."""
    if not section_dir.is_dir() or not session.isdigit():
        return None
    target = int(session)
    for child in sorted(section_dir.iterdir()):
        if child.is_dir() and session_number(child.name) == target:
            return child
    return None


def discover_local_sessions(repo_root: Path) -> list[str]:
    """The session numbers a CHECKOUT holds, across every discovered section.

    The local-checkout twin of `discovery.discover_sessions`, which asks GitHub for a
    recursive tree instead. A caller that has already cloned the repo to copy files out of
    it has the answer on disk, and the API's copy of it can only be the same or staler."""
    return [
        str(n)
        for n in sorted(
            {n for parent, _, n in session_dirs(_local_dir_paths(repo_root)) if parent}
        )
    ]


def discover_sections(repo_root: Path) -> list[str]:
    """Any top-level directory containing at least one ordinal-prefixed subdirectory is
    a releasable section - no declared config, the directory structure is the only
    source of truth. Sorted for a deterministic order.

    The local-checkout transport of the session_dirs rule; dsl_course.discovery is the
    API-side one."""
    return sorted(
        {parent for parent, _, _ in session_dirs(_local_dir_paths(repo_root)) if parent}
    )


# How a slug is SHOWN. `identifier("assignment-5")` is "Assignment 5": the bold half of a
# schedule row, the kicker over an assignment page, and the label beside the name in a
# gradebook. One spelling, because the same assignment is read in all three places.


def identifier(slug: str) -> str:
    """The slug's own name, title-cased: `assignment-3-project` -> `Assignment 3 Project`."""
    return slug.replace("-", " ").title()


# Separators faculty put between a row's identifier and its name. Two dash characters are
# in live sources already (`Assignment 1 - ...` and `Assignment 1 — ...`), which is exactly
# why this is a set and not a `-`.
_NAME_SEPARATORS = "-\u2013\u2014:|"


def row_name(declared: str, identifier: str) -> str:
    """A row's NAME out of what faculty wrote, given the identifier the site already shows
    in bold beside it - so the pair reads "Session 3 / Probability theory" and never
    "Session 3 / Session 3".

    Faculty conventionally repeat the identifier: a template README opens `# Assignment 1 -
    linear regression from scratch`, and a `releases:` entry is as likely to say
    `title: Lab 1` as to name the lab. Printed whole under the identifier that reads
    "Assignment 1 / Assignment 1 - linear regression from scratch" and "Lab 1 / Lab 1", so
    drop the prefix and whatever separates it.

    Text that does NOT open with the identifier (`Group project - an end-to-end modelling
    report`) is the name already and is returned as it stands. Casefolded, so text that
    differs from the identifier only in capitalisation still matches."""
    name = declared.strip()
    if name.casefold().startswith(identifier.casefold()):
        rest = name[len(identifier) :].lstrip()
        # Only when a separator actually follows: `Assignment 10` must not be read as
        # `Assignment 1` plus the name "0".
        if rest[:1] in tuple(_NAME_SEPARATORS):
            return rest[1:].strip()
        if not rest:
            return ""
    return name


# The `run-name` prefix of a Scheduled release run scoped to ONE semester
# (`workflows_render._SCOPED_SEMESTER`), which `cadence` reads off the runs listing's
# `display_title`. Such a run is neither a driver firing nor a tick of the whole course:
# counted, a push in semester A would shrink the gap a late release in semester B is measured
# by, and hide it.
SCOPED_RUN_TITLE = "Scheduled release for cohort"
# The `client_payload.driver` of the Scheduled release and Sync membership a migration
# dispatches after its unpause, in place of the ticks the pause dropped: the run history
# tells a catch-up from a real config push by it. With a `semester_org`, the run is scoped
# to that semester like a semester-config push.
MIGRATE_DRIVER = "migrate"
