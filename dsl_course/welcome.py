"""The SYSTEM-owned cohort-repo seeding, and the template reader it shares.

Split out of bootstrap_course so `seed.refresh` can re-push a live cohort's onboarding
workflows, config samples and classroom-config system files on its nightly run:
bootstrap_course imports seed, so seed cannot import bootstrap_course back - this module
is what both sides may import.

Everything this module writes is SYSTEM-owned, and that is the whole rule for what may
live here: a cohort's own config (students.csv, teams.csv, schedule.yml, people.yml,
grades/) is seeded create-if-missing by bootstrap_course and must never be refreshed from
a template, or a nightly run would clobber a live roster.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from .central import CENTRAL, pin_central_ref
from .gh_contents import put_files
from .log import log_err, log_ok
from .repos import ensure_label
from .roster import CONFIG_REPO

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
EXAMPLE_COHORT = ROOT / "example-course" / "cohort-org"
EXAMPLE_COURSE = ROOT / "example-course" / "course-org"

# Every user-editable file in classroom-config ships as a PAIR under one rule: `<file>` is
# a minimal commented scaffold, seeded once and never rewritten; `<file>.sample` is a
# filled, realistic example, always converged.
#
# The SCAFFOLD half - the file faculty fill in. `{tag}`/`{year}`/`{year_next}` are
# rendered for this cohort, so every example in a scaffold is copy-paste-correct.
CLASSROOM_SCAFFOLDS = {
    "students.csv": "classroom-config/students.csv",
    "teams.csv": "classroom-config/teams.csv",
    "schedule.yml": "classroom-config/schedule.yml",
    "people.yml": "classroom-config/people.yml",
}

# The SAMPLE half - DERIVED, not enumerated: every regular file in the worked example
# cohort ships as `<its path>.sample`. Deriving is what makes example-course/README.md's
# "every file in cohort-org/ is seeded" claim true by construction; enumerating it once
# silently dropped the team-graded grades table. The samples are therefore not authored
# twice - they ARE the worked example the docs link to, and tests/test_bootstrap_seeding.py
# parses each one with the real parser so none can go schema-stale.
CLASSROOM_SAMPLES = {
    f"{rel}.sample": rel
    for rel in sorted(
        p.relative_to(EXAMPLE_COHORT).as_posix()
        for p in EXAMPLE_COHORT.rglob("*")
        if p.is_file()
    )
    # dotfiles are plumbing (.gitkeep and friends), never reference material
    if not any(part.startswith(".") for part in rel.split("/"))
}


@cache
def template(rel: str) -> str:
    """Read a seeded template file (templates/<rel>) as text.

    Everything under templates/ is content pushed into a course/cohort repo, kept in real
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
SHARED_SCRIPT = "welcome/_shared-script.js"


def welcome_workflow(rel: str) -> str:
    """A welcome-repo workflow template, with the shared github-script helpers spliced in.

    THE reader for these two files: seeding, refreshing and the tests all go through here,
    so nothing can ship (or assert about) a workflow whose script is only half written.
    Indentation comes from the marker's own line, because the script is a YAML block scalar
    and a helper at the wrong column is a parse error in every cohort at once."""
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
    return "".join(out)


@cache
def example_cohort_file(rel: str) -> str:
    """Read a file from the worked example cohort (example-course/cohort-org/<rel>).

    Seeded verbatim as a `.sample`: never str.format-rendered, because a worked example is
    a real cohort's file (hertie-dsl-demo-f2026), not a scaffold to fill in."""
    return (EXAMPLE_COHORT / rel).read_text(encoding="utf-8")


@cache
def example_course_file(rel: str) -> str:
    """Read a file from the worked example COURSE org (example-course/course-org/<rel>).

    The course tier of the same rule, for the one file that is a seeded scaffold/sample
    pair rather than pure reference material: `scaffold` derives SYLLABUS.md.sample from
    this tree's SYLLABUS.md, so the syllabus faculty receive IS the one the docs link to.
    The rest of course-org/ is documentation - linked from docs/, never seeded - but it is
    parsed by the real readers in tests/test_bootstrap_seeding.py all the same, so it
    cannot go schema-stale in silence either."""
    return (EXAMPLE_COURSE / rel).read_text(encoding="utf-8")


# The ROUTING labels: each Join form declares one (`labels:` in its ISSUE_TEMPLATE) and
# the matching workflow gates on it (`if: contains(github.event.issue.labels.*.name, ...)`).
# GitHub silently DROPS a form-declared label the repo doesn't have, and nothing else ever
# created these - so every Join issue skipped both workflows: no redaction, no comment, no
# needs-review, a green "skipped" run. Seeded by refresh_welcome_workflows below; the
# names are pinned to the forms and the workflow guards by tests/test_welcome_templates.py.
WELCOME_LABELS = (
    ("onboarding", "0e8a16", "Join course issue - routes the Onboard student workflow"),
    ("team-formation", "1d76db", "Join team issue - routes the Form team workflow"),
)


def refresh_welcome_workflows(org: str) -> int:
    """Re-push a cohort's welcome-repo machinery (onboarding workflows + the issue forms
    they parse) from the current templates, as ONE commit - and ensure the routing labels
    those forms declare exist in the repo. Called both at bootstrap and on every refresh,
    so a fix reaches running cohorts; put_files skips whatever is already identical and
    commits nothing at all when everything is.

    A workflow and the form it parses must move together (field ids are a contract between
    them), so one commit is also the honest unit here: the intermediate state where one has
    landed and the other hasn't is not one anybody should be able to check out.

    Returns the failure count, so a caller (seed.refresh) can go red rather than report an
    onboarding repo it never managed to converge."""
    # Everything under .github/ here is SYSTEM-owned: the onboarding workflows and the
    # issue forms they parse (field ids must stay in lockstep with the workflow), so
    # these refresh on every run.
    if not put_files(
        org,
        "welcome",
        {
            ".github/workflows/onboard.yml": welcome_workflow(
                "welcome/onboard.yml"
            ).encode(),
            ".github/ISSUE_TEMPLATE/01-join-course.yml": template(
                "welcome/ISSUE_TEMPLATE/01-join-course.yml"
            ).encode(),
            ".github/workflows/team-formation.yml": welcome_workflow(
                "welcome/team-formation.yml"
            ).encode(),
            ".github/ISSUE_TEMPLATE/02-join-team.yml": template(
                "welcome/ISSUE_TEMPLATE/02-join-team.yml"
            ).encode(),
            ".github/ISSUE_TEMPLATE/config.yml": template(
                "welcome/ISSUE_TEMPLATE/config.yml"
            ).encode(),
        },
        "ci: refresh onboarding workflows + Join forms",
        # The forms were renamed to control the issue-chooser ordering (01-/02- prefix);
        # retire the old filenames on live cohorts or the chooser shows both generations.
        delete=(
            ".github/ISSUE_TEMPLATE/join.yml",
            ".github/ISSUE_TEMPLATE/join-team.yml",
        ),
    ):
        log_err(f"welcome-repo files not written in {org}")
        failures = 1
    else:
        failures = 0
    # The labels are as load-bearing as the files: without them both workflows are
    # `skipped` on every Join issue. ensure_label is create-only and idempotent, so a
    # cohort that has them is written nothing.
    for name, color, description in WELCOME_LABELS:
        if not ensure_label(org, "welcome", name, color=color, description=description):
            failures += 1
    if failures:
        return failures
    log_ok("welcome repo workflows + Join forms + routing labels up to date")
    return 0


def refresh_classroom_samples(org: str) -> int:
    """Converge a cohort's classroom-config `*.sample` files on the worked example.

    Samples are machine-owned reference material - the engine never ingests them (only the
    un-suffixed names), and activation is copying rows across - so unlike the scaffolds
    they are written unconditionally rather than seed-if-absent. `put_files` compares blob
    shas, so an already-current cohort is written nothing. Called both at bootstrap and on
    the nightly refresh, so a cohort seeded last semester picks up today's examples.

    All of them in ONE commit: they are regenerated from a single worked example, so an
    update to that example moves the whole set at once.

    Returns 1 if that commit didn't land, so seed.refresh can go red rather than report an
    org it never converged."""
    if not put_files(
        org,
        CONFIG_REPO,
        {
            path: example_cohort_file(source).encode()
            for path, source in CLASSROOM_SAMPLES.items()
        },
        "docs: refresh classroom-config samples from the worked example course",
    ):
        log_err(f"classroom-config samples not written in {org}")
        return 1
    log_ok("classroom-config samples up to date")
    return 0


# The SYSTEM-owned half of a cohort's classroom-config: the schema contract faculty read,
# and the three workflows that make the repo act on what they put in it, as
# `(path in the repo, template file)`.
#
# HARD INVARIANT: nothing the cohort edits may join this table. students.csv, teams.csv,
# schedule.yml, people.yml and grades/ hold the cohort's LIVE state (enrol codes, onboarded
# handles, returned marks); they are seeded create-if-missing by bootstrap_course and stay
# that way. Adding one here would have the nightly refresh overwrite it every night.
# tests/test_bootstrap_seeding.py pins this set exactly, so an addition fails loud.
CLASSROOM_SYSTEM_FILES = (
    ("README.md", "classroom-config/README.md"),
    (".github/workflows/dispatch-sync.yml", "classroom-config/dispatch-sync.yml"),
    (
        ".github/workflows/dispatch-sync-site.yml",
        "classroom-config/dispatch-sync-site.yml",
    ),
    (
        ".github/workflows/validate-schedule.yml",
        "classroom-config/validate-schedule.yml",
    ),
)


def classroom_system_files(central_ref: str) -> dict[str, bytes]:
    """CLASSROOM_SYSTEM_FILES rendered for one cohort, read at call time so importing this
    module never touches the filesystem.

    Placeholders rather than `str.format`, because these files are full of `${{ }}` GitHub
    expressions that `format` would try to interpret. The whole set goes through
    `pin_central_ref`, which refuses a ref the central repo does not have - the schedule
    validator checks the toolkit out at it, and the set is written as one commit anyway.
    """
    return {
        path: pin_central_ref(template(rel), central_ref)
        .replace("__CENTRAL__", CENTRAL)
        .encode()
        for path, rel in CLASSROOM_SYSTEM_FILES
    }


def refresh_cohort_pointer(org: str, course_org: str) -> int:
    """Re-push a cohort's `.github/dsl-course.yml` - the pointer its classroom-config
    dispatchers read to find which course org to fire Sync membership / Sync site at.

    SYSTEM-owned, but it used to be written ONLY by Bootstrap cohort's own wiring, so it
    froze the day the cohort was created: every live cohort's copy still dated from the
    org rename in August while the template had moved on. Same bug class as the cohort
    landing pages (see seed.refresh) - a file documented as converged that in fact never
    was. `put_files` compares blob shas, so a cohort already current is written nothing.

    Returns 1 if the commit didn't land: without a resolvable pointer the dispatchers
    cannot find the course org, and the cohort's syncs stop firing."""
    if not put_files(
        org,
        ".github",
        {
            "dsl-course.yml": template("cohort/dsl-course.yml")
            .format(course=course_org, org=org)
            .encode()
        },
        "ci: refresh cohort -> course pointer",
    ):
        log_err(f"cohort -> course pointer not written to {org}/.github")
        return 1
    return 0


def refresh_classroom_system_files(org: str, central_ref: str) -> int:
    """Re-push a cohort's SYSTEM-owned classroom-config files (CLASSROOM_SYSTEM_FILES).

    Called both at bootstrap and on the nightly refresh, so a fix to a dispatcher or to
    the schema contract reaches running cohorts. It used to run only inside "Bootstrap
    cohort", which meant a template fix landed on a live cohort only if someone thought to
    run that workflow again - three live cohorts drifted a whole semester that way.
    `put_files` compares blob shas, so a cohort already current is written nothing.

    A failed write here is not cosmetic: without dispatch-sync*.yml a cohort's membership
    and site syncs never fire. Returns 1 if the commit didn't land, so callers
    (setup_cohort_extras, seed.refresh) go red rather than report a converged cohort."""
    if not put_files(
        org,
        CONFIG_REPO,
        classroom_system_files(central_ref),
        "ci: refresh classroom-config contract + dispatchers",
    ):
        log_err(f"classroom-config system files not written in {org}")
        return 1
    log_ok("classroom-config ready (config preserved, dispatchers refreshed)")
    return 0
