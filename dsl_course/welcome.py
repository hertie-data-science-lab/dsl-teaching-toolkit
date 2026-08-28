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

from .central import CENTRAL, CENTRAL_REF
from .gh_contents import put_files
from .log import log_err, log_ok
from .roster import CONFIG_REPO

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
EXAMPLE_COHORT = ROOT / "example-course" / "cohort-org"

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


@cache
def example_cohort_file(rel: str) -> str:
    """Read a file from the worked example cohort (example-course/cohort-org/<rel>).

    Seeded verbatim as a `.sample`: never str.format-rendered, because a worked example is
    a real cohort's file (hertie-dsl-demo-f2026), not a scaffold to fill in."""
    return (EXAMPLE_COHORT / rel).read_text(encoding="utf-8")


def refresh_welcome_workflows(org: str) -> int:
    """Re-push a cohort's welcome-repo machinery (onboarding workflows + the issue forms
    they parse) from the current templates, as ONE commit. Called both at bootstrap and on
    every refresh, so a fix reaches running cohorts; put_files skips whatever is already
    identical and commits nothing at all when everything is.

    A workflow and the form it parses must move together (field ids are a contract between
    them), so one commit is also the honest unit here: the intermediate state where one has
    landed and the other hasn't is not one anybody should be able to check out.

    Returns 1 if that commit didn't land, so a caller (seed.refresh) can go red rather than
    report an onboarding repo it never managed to converge."""
    # Everything under .github/ here is SYSTEM-owned: the onboarding workflows and the
    # issue forms they parse (field ids must stay in lockstep with the workflow), so
    # these refresh on every run.
    if not put_files(
        org,
        "welcome",
        {
            ".github/workflows/onboard.yml": template("welcome/onboard.yml").encode(),
            ".github/ISSUE_TEMPLATE/01-join-course.yml": template(
                "welcome/ISSUE_TEMPLATE/01-join-course.yml"
            ).encode(),
            ".github/workflows/team-formation.yml": template(
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
        return 1
    log_ok("welcome repo workflows + Join forms up to date")
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


def _validate_schedule_workflow() -> str:
    """The classroom-config schedule validator, with the central repo pinned into it.

    Placeholders rather than `str.format`, because the file is full of `${{ }}` GitHub
    expressions that `format` would try to interpret."""
    return (
        template("classroom-config/validate-schedule.yml")
        .replace("__CENTRAL_REF__", CENTRAL_REF)
        .replace("__CENTRAL__", CENTRAL)
    )


# The SYSTEM-owned half of a cohort's classroom-config: the schema contract faculty read,
# and the three workflows that make the repo act on what they put in it. `(path, content
# reader)` - the content is read lazily, at call time, so importing this module never
# touches the filesystem.
#
# HARD INVARIANT: nothing the cohort edits may join this table. students.csv, teams.csv,
# schedule.yml, people.yml and grades/ hold the cohort's LIVE state (enrol codes, onboarded
# handles, returned marks); they are seeded create-if-missing by bootstrap_course and stay
# that way. Adding one here would have the nightly refresh overwrite it every night.
# tests/test_bootstrap_seeding.py pins this set exactly, so an addition fails loud.
CLASSROOM_SYSTEM_FILES = (
    ("README.md", lambda: template("classroom-config/README.md")),
    (
        ".github/workflows/dispatch-sync.yml",
        lambda: template("classroom-config/dispatch-sync.yml"),
    ),
    (
        ".github/workflows/dispatch-sync-site.yml",
        lambda: template("classroom-config/dispatch-sync-site.yml"),
    ),
    (".github/workflows/validate-schedule.yml", _validate_schedule_workflow),
)


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


def refresh_classroom_system_files(org: str) -> int:
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
        {path: content().encode() for path, content in CLASSROOM_SYSTEM_FILES},
        "ci: refresh classroom-config contract + dispatchers",
    ):
        log_err(f"classroom-config system files not written in {org}")
        return 1
    log_ok("classroom-config ready (config preserved, dispatchers refreshed)")
    return 0
