"""The SYSTEM-owned semester-repo seeding, and the template reader it shares.

Split out of bootstrap_course so `seed.refresh` can re-push a live semester's onboarding
workflows and semester-config system files on its nightly run:
bootstrap_course imports seed, so seed cannot import bootstrap_course back - this module
is what both sides may import.

Everything this module writes is SYSTEM-owned, and that is the whole rule for what may
live here: a semester's own config (students.csv, teams.csv, schedule.yml, instructors.yml) is
seeded create-if-missing by bootstrap_course and must never be refreshed from a template,
or a nightly run would clobber a live roster.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from pathlib import Path

from . import records
from .central import CENTRAL, pausable, pin_central_ref
from .course import JOIN_REPO
from .gh_contents import get_file_content, put_file, put_files
from .grades import TEAM_LOCK_PATH, parse_team_lock
from .log import log_err, log_ok
from .repos import ensure_label
from .roster import CONFIG_REPO

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
EXAMPLE_COURSE = ROOT / "example-course" / "course-org"

# Every user-editable file in semester-config is seeded as a minimal commented scaffold,
# once, and never rewritten. Filled examples are not seeded (decision 0010): each scaffold
# links the worked example semester in `example-course/cohort-org/` instead. `{tag}`/`{year}`/`{year_next}` are
# rendered for this semester, so every example in a scaffold is copy-paste-correct.
CONFIG_SCAFFOLDS = {
    "students.csv": "semester-config/students.csv",
    "teams.csv": "semester-config/teams.csv",
    "schedule.yml": "semester-config/schedule.yml",
    "instructors.yml": "semester-config/instructors.yml",
}


@cache
def template(rel: str) -> str:
    """Read a seeded template file (templates/<rel>) as text.

    Everything under templates/ is content pushed into a course/semester repo, kept in real
    files rather than Python literals so faculty & instructors can read (and PR) the thing
    they'll actually receive. Most are seeded verbatim; the few that carry `{placeholders}`
    are rendered with str.format (see bootstrap_course._course_metadata)."""
    return (TEMPLATES / rel).read_text(encoding="utf-8")


# The JavaScript both onboarding workflows run is github-script with no npm deps, so the
# CSV reader/writer and the "fail visibly" helper are hand-rolled - and were hand-rolled
# TWICE, once per workflow file, against one file format, with a test comparing the copies
# byte for byte to catch the day they stopped agreeing. One copy now lives beside them and
# is spliced in at this marker, which is a JS comment so an un-spliced template is still
# valid YAML and still valid JavaScript.
SHARED_SCRIPT_MARK = "// {shared_script}"
SHARED_SCRIPT = "join/_shared-script.js"


def join_workflow(rel: str) -> str:
    """A join-repo workflow template, with the shared github-script helpers spliced in.

    THE reader for these two files: seeding, refreshing and the tests all go through here,
    so nothing can ship (or assert about) a workflow whose script is only half written.
    Indentation comes from the marker's own line, because the script is a YAML block scalar
    and a helper at the wrong column is a parse error in every semester at once. The
    config repo's name is substituted, never spelt in the script."""
    out = []
    for line in template(rel).splitlines(keepends=True):
        if line.strip() != SHARED_SCRIPT_MARK:
            out.append(line)
            continue
        pad = line[: len(line) - len(line.lstrip())]
        out += [
            f"{pad}{shared}\n" if shared.strip() else "\n"
            for shared in template(SHARED_SCRIPT).rstrip("\n").split("\n")
        ]
    return pausable(
        "".join(out)
        .replace("__CONFIG_REPO__", CONFIG_REPO)
        .replace("__LOCK__", TEAM_LOCK_PATH)
    )


@cache
def example_course_file(rel: str) -> str:
    """Read a file from the worked example COURSE org (example-course/course-org/<rel>).

    The one file of the worked example that is seeded rather than only linked:
    `scaffold` derives `.system/SYLLABUS.md.sample` from
    this tree's SYLLABUS.md, so the syllabus faculty receive IS the one the docs link to.
    The rest of course-org/ is documentation - linked from docs/, never seeded - but it is
    parsed by the real readers in tests/test_bootstrap_seeding.py all the same, so it
    cannot go schema-stale in silence either."""
    return (EXAMPLE_COURSE / rel).read_text(encoding="utf-8")


# The ROUTING labels: each Join form declares one (`labels:` in its ISSUE_TEMPLATE) and
# the matching workflow gates on it (`if: contains(github.event.issue.labels.*.name, ...)`).
# GitHub silently DROPS a form-declared label the repo doesn't have, and nothing else ever
# created these - so every Join issue skipped both workflows: no redaction, no comment, no
# needs-review, a green "skipped" run. Seeded by refresh_join_workflows below; the
# names are pinned to the forms and the workflow guards by tests/test_join_templates.py.
JOIN_LABELS = (
    ("onboarding", "0e8a16", "Join course issue - routes the Onboard student workflow"),
    ("team-formation", "1d76db", "Join team issue - routes the Form team workflow"),
    # Not a routing label: what the Form team workflow closes a Join team issue with when
    # the STUDENT can put it right (a team that does not exist, a name taken, a window
    # shut). Seeded so it reads as that rather than as a staff queue - `needs-review` is
    # kept for what somebody on the teaching team has to act on.
    (
        "team-refused",
        "e4e669",
        "Join team request refused - the comment says how to fix it",
    ),
)


# The Join-team form's Assignment field is the one part of a seeded form that is not the
# same in every semester: the slugs it should offer are this semester's, and which of them a
# student may act on changes with the calendar. Everything outside these markers is the
# form as reviewed; everything between them is regenerated per semester, the same idiom (and
# the same "an instructor who deleted the markers meant it" rule) as
# `profile_readme.splice_repo_table`.
JOIN_TEAM_FORM = "join/ISSUE_TEMPLATE/02-join-team.yml"
ASSIGNMENT_FIELD_START = "# dsl:assignment-field:start"
ASSIGNMENT_FIELD_END = "# dsl:assignment-field:end"


def open_formations(lock_text: str) -> dict[str, str]:
    """The assignments in a semester's `assignments.lock.yml` whose team-formation window is
    OPEN, sorted, each with its page on the semester site (`team_formation_page`, "" where
    the lock carries none).

    A filter over `grades.parse_team_lock`, which lives beside the writer of that file: the
    format is hand-rolled for a line scanner, and a second scanner here would be a second
    opinion about a shape one module decides.

    `open` is written only for a self-select group assignment inside its window, so this
    needs no second opinion about the shape either: every other assignment is one the form
    would refuse anyway, and offering it in the dropdown would be inviting a student to be
    refused."""
    return {
        key: entry.get("team_formation_page", "")
        for key, entry in sorted(parse_team_lock(lock_text).items())
        if entry.get("team_formation_window", "").lower() == "open"
    }


# The form's own first sentence, as the reviewed template spells it. It names the page
# without being able to link it, because the page's URL is per semester - so it is the
# wording a semester whose lock carries none receives, and the anchor the real links below
# are spliced over. Pinned against the template by a test: a rewording there with no
# rewording here would silently stop the splice.
TEAM_LIST_SENTENCE = (
    "The teams that already exist, and how much room each has left, are listed on the "
    "assignment's page on the semester site."
)


def _team_list_header(opened: Mapping[str, str]) -> str:
    """The header sentence, linking the page a student can actually open.

    One open assignment is the ordinary case and gets one link inside the sentence; several
    get a line each, because "listed on these two pages" with both links inline is a
    sentence nobody reads to the end of. A slug whose page is not known is left out rather
    than linked to nowhere, and a header that knows none of them is the template's own."""
    links = [(s, u) for s, u in opened.items() if u]
    if not links:
        return TEAM_LIST_SENTENCE
    lead = "The teams that already exist, and how much room each has left, are listed"
    if len(links) == 1:
        return f"{lead} on [the assignment's page]({links[0][1]})."
    listed = "\n".join(f"        - [{slug}]({url})" for slug, url in links)
    return f"{lead} on each assignment's page:\n\n{listed}"


def join_team_form(opened: Mapping[str, str]) -> str:
    """The Join-team issue form for a semester whose open assignments are `opened`'s keys
    (`open_formations`), in order.

    With any, the Assignment field becomes a dropdown of exactly those: a free-text slug is
    a guess, and a guess that misses is answered by a workflow comment some minutes later,
    which is the slowest possible way to learn you typed a dash wrong.

    With none, the template's own free-text field is returned untouched. A GitHub dropdown
    needs at least one option - an empty `options:` list is a form GitHub refuses to
    render, which would take the Join-team route away from a semester entirely rather than
    merely leave it awkward.

    `opened`'s values link each assignment's page on the semester site from the header ("" =
    not known; the lock's `team_formation_page`). The form tells a student to type a
    team's name "exactly as that page spells it", so a page they cannot reach in one click
    is an instruction they cannot follow."""
    form = template(JOIN_TEAM_FORM).replace(
        TEAM_LIST_SENTENCE, _team_list_header(opened)
    )
    if not opened:
        return form
    start = form.find(ASSIGNMENT_FIELD_START)
    end = form.find(ASSIGNMENT_FIELD_END)
    if start == -1 or end == -1 or end < start:
        return form
    options = "\n".join(f"        - {slug}" for slug in opened)
    block = (
        f"{ASSIGNMENT_FIELD_START} - AUTO-GENERATED from this semester's\n"
        "  # `semester-config/.system/assignments.lock.yml`: the assignments open for team\n"
        "  # formation right now. Edits between these markers are overwritten.\n"
        "  - type: dropdown\n"
        "    id: assignment\n"
        "    attributes:\n"
        "      label: Assignment\n"
        "      description: The group assignment you are forming a team for.\n"
        "      options:\n"
        f"{options}\n"
        "    validations:\n"
        "      required: true\n"
        f"  {ASSIGNMENT_FIELD_END}"
    )
    return form[:start] + block + form[end + len(ASSIGNMENT_FIELD_END) :]


def _open_formations(org: str) -> dict[str, str]:
    """`open_formations` for a live semester, or none of them.

    Every way of not reading the lock lands on the free-text fallback: a semester seeded
    before the file existed, one whose sync has not run yet, and a read that failed for a
    reason nobody here can act on. The refresh's job is to leave a WORKING form behind, and
    the free-text one has worked for every semester so far."""
    try:
        text = get_file_content(org, CONFIG_REPO, TEAM_LOCK_PATH)
    except RuntimeError as exc:
        log_err(
            f"could not read {TEAM_LOCK_PATH} in {org} ({exc}) - the Join-team form is "
            f"seeded with a free-text Assignment field this run"
        )
        return {}
    return open_formations(text) if text else {}


JOIN_TEAM_FORM_PATH = ".github/ISSUE_TEMPLATE/02-join-team.yml"


def refresh_join_team_form(org: str) -> int:
    """Re-push JUST the Join-team form, so its Assignment dropdown offers what the semester's
    lock says is open RIGHT NOW. Returns the failure count.

    The tick that moves the window calls this (`scheduler._team_formation_phase`), and it
    has to: the Assignment field is `required`, so a student whose second group assignment
    opened this quarter of an hour cannot file the issue for it AT ALL until the form
    offers the slug. Left to the nightly refresh, that is up to 24 hours during which the
    site shows a callout and the mail links a chooser that refuses them.

    ONE file, deliberately, where `refresh_join_workflows` pushes six and ensures three
    labels: nothing else here moves with the calendar, and this runs on a semester's clock
    rather than on a deploy. `put_file` compares blob shas, so a window whose options have
    not actually changed is written nothing and commits nothing.

    The full refresh keeps doing what it does - bootstrap and the nightly run converge the
    whole set, this one keeps the dropdown honest between them."""
    if put_file(
        org,
        JOIN_REPO,
        JOIN_TEAM_FORM_PATH,
        join_team_form(_open_formations(org)).encode(),
        "ci: refresh the Join-team form's open assignments",
    ):
        return 0
    log_err(
        f"the Join-team form in {org} could not be written - it offers whatever it last "
        f"offered, so a window that has just opened is not selectable until the nightly "
        f"refresh"
    )
    return 1


def refresh_join_workflows(org: str) -> int:
    """Re-push a semester's join-repo machinery (onboarding workflows + the issue forms
    they parse) from the current templates, as ONE commit - and ensure the routing labels
    those forms declare exist in the repo. Called both at bootstrap and on every refresh,
    so a fix reaches running semesters; put_files skips whatever is already identical and
    commits nothing at all when everything is.

    A workflow and the form it parses must move together (field ids are a contract between
    them), so one commit is also the honest unit here: the intermediate state where one has
    landed and the other hasn't is not one anybody should be able to check out.

    The Join-team form is the one file here that is not the template verbatim: its
    Assignment field is rendered from the semester's own lock (`join_team_form`), so this runs
    WITH the calendar rather than only with a template change - which is why `put_files`
    comparing blob shas matters: a semester whose windows have not moved is written nothing.
    A window that turns BETWEEN these runs is not left to wait for the next one:
    `refresh_join_team_form` pushes that one file on the tick that moved the lock.

    Returns the failure count, so a caller (seed.refresh) can go red rather than report an
    onboarding repo it never managed to converge."""
    # Everything under .github/ here is SYSTEM-owned: the onboarding workflows and the
    # issue forms they parse (field ids must stay in lockstep with the workflow), so
    # these refresh on every run.
    if not put_files(
        org,
        JOIN_REPO,
        {
            ".github/workflows/onboard.yml": join_workflow("join/onboard.yml").encode(),
            ".github/ISSUE_TEMPLATE/01-join-course.yml": template(
                "join/ISSUE_TEMPLATE/01-join-course.yml"
            ).encode(),
            ".github/workflows/team-formation.yml": join_workflow(
                "join/team-formation.yml"
            ).encode(),
            JOIN_TEAM_FORM_PATH: join_team_form(_open_formations(org)).encode(),
            ".github/ISSUE_TEMPLATE/config.yml": template(
                "join/ISSUE_TEMPLATE/config.yml"
            ).encode(),
        },
        "ci: refresh onboarding workflows + Join forms",
        # The forms were renamed to control the issue-chooser ordering (01-/02- prefix);
        # retire the old filenames on live semesters or the chooser shows both generations.
        delete=(
            ".github/ISSUE_TEMPLATE/join.yml",
            ".github/ISSUE_TEMPLATE/join-team.yml",
        ),
    ):
        log_err(f"join-repo files not written in {org}")
        failures = 1
    else:
        failures = 0
    # The labels are as load-bearing as the files: without them both workflows are
    # `skipped` on every Join issue. ensure_label is create-only and idempotent, so a
    # semester that has them is written nothing.
    for name, color, description in JOIN_LABELS:
        if not ensure_label(org, JOIN_REPO, name, color=color, description=description):
            failures += 1
    if failures:
        return failures
    log_ok("join repo workflows + Join forms + routing labels up to date")
    return 0


# The SYSTEM-owned half of a semester's semester-config: the schema contract faculty read,
# and the workflows that make the repo act on what they put in it, as
# `(path in the repo, template file)`.
#
# HARD INVARIANT: nothing the semester edits may join this table. students.csv, teams.csv,
# schedule.yml and instructors.yml hold the semester's LIVE state (enrol codes, onboarded
# handles); they are seeded create-if-missing by bootstrap_course and stay that way.
# Adding one here would have the nightly refresh overwrite it every night.
# tests/test_bootstrap_seeding.py pins this set exactly, so an addition fails loud.
CONFIG_SYSTEM_FILES = (
    ("README.md", "semester-config/README.md"),
    (".github/workflows/dispatch-sync.yml", "semester-config/dispatch-sync.yml"),
    (
        ".github/workflows/dispatch-sync-site.yml",
        "semester-config/dispatch-sync-site.yml",
    ),
    (
        ".github/workflows/dispatch-scheduled-release.yml",
        "semester-config/dispatch-scheduled-release.yml",
    ),
    (
        ".github/workflows/dispatch-send-codes.yml",
        "semester-config/dispatch-send-codes.yml",
    ),
    (
        ".github/workflows/validate-schedule.yml",
        "semester-config/validate-schedule.yml",
    ),
)


def config_system_files(central_ref: str) -> dict[str, bytes]:
    """CONFIG_SYSTEM_FILES rendered for one semester, read at call time so importing this
    module never touches the filesystem.

    Placeholders rather than `str.format`, because these files are full of `${{ }}` GitHub
    expressions that `format` would try to interpret. The whole set goes through
    `pin_central_ref`, which refuses a ref the central repo does not have - the schedule
    validator checks the toolkit out at it, and the set is written as one commit anyway.
    """
    return {
        path: pausable(pin_central_ref(template(rel), central_ref))
        .replace("__CENTRAL__", CENTRAL)
        .replace("__CONFIG_REPO__", CONFIG_REPO)
        .replace("__POINTER__", records.path("pointer"))
        .encode()
        for path, rel in CONFIG_SYSTEM_FILES
    }


def refresh_semester_pointer(org: str, course_org: str) -> int:
    """Re-push a semester's `semester-config/.system/dsl-course.yml` - the pointer its
    dispatchers read to find which course org to fire Sync membership / Sync site at.

    SYSTEM-owned, but it used to be written ONLY by Bootstrap semester's own wiring, so it
    froze the day the semester was created: every live semester's copy still dated from the
    org rename in August while the template had moved on. Same bug class as the semester
    landing pages (see seed.refresh) - a file documented as converged that in fact never
    was. `put_files` compares blob shas, so a semester already current is written nothing.

    Returns 1 if the commit didn't land: without a resolvable pointer the dispatchers
    cannot find the course org, and the semester's syncs stop firing."""
    if not put_files(
        org,
        CONFIG_REPO,
        {
            records.path("pointer"): template("semester/dsl-course.yml")
            .format(course=course_org, org=org)
            .encode()
        },
        "ci: refresh semester -> course pointer",
    ):
        log_err(f"semester -> course pointer not written to {org}/{CONFIG_REPO}")
        return 1
    return 0


def refresh_config_system_files(org: str, central_ref: str) -> int:
    """Re-push a semester's SYSTEM-owned semester-config files (CONFIG_SYSTEM_FILES).

    Called both at bootstrap and on the nightly refresh, so a fix to a dispatcher or to
    the schema contract reaches running semesters. It used to run only inside "Bootstrap
    semester", which meant a template fix landed on a live semester only if someone thought to
    run that workflow again - three live semesters drifted a whole semester that way.
    `put_files` compares blob shas, so a semester already current is written nothing.

    A failed write here is not cosmetic: without dispatch-sync*.yml a semester's membership
    and site syncs never fire. Returns 1 if the commit didn't land, so callers
    (setup_semester_extras, seed.refresh) go red rather than report a converged semester."""
    if not put_files(
        org,
        CONFIG_REPO,
        config_system_files(central_ref),
        "ci: refresh semester-config contract + dispatchers",
    ):
        log_err(f"semester-config system files not written in {org}")
        return 1
    log_ok("semester-config ready (config preserved, dispatchers refreshed)")
    return 0
