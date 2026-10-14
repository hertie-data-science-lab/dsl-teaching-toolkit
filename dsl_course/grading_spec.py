"""dsl-course grading spec -- an assignment's own definition, `grading_config.yml` on its
course template's `solution` branch.

Parsed into a `GradingSpec`, its run settings resolved through the cascade (`settings`),
and the faults it raises for the digest. Also the bridge to the grading sheet: the
`SheetSpec` a definition gives (`sheet_spec`, `sheet_specs`), and the semester's sheet
faults, which need both.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit

import yaml

from . import gh_teams, run, schedule, settings
from .course import (
    CONFIG_REPO,
    NO_STARTER,
    NO_TEAMS,
    SEMESTER_FILE,
    SOLUTION_BRANCH,
    creates_unit_repos,
    identifier,
    row_name,
)
from .discovery import (
    assignment_rows,
)
from .faults import ConfigFault, RunSettingInTemplate
from .gh_contents import (
    get_file_content,
    yaml_mark_line,
)
from .grading_sheet import (
    SheetSpec,
    _plain,
    _Shape,
    key_lines,
    sheet_faults,
    sheet_path,
)
from .log import (
    log_err,
)
from .setting_readers import (
    SPEC_KEYS,
    Dropped,
    as_decimal,
    question_files,
    read_settings,
)

# ------------------------------------------------- the assignment's own definition

# `grading_config.yml`, on the course template's `solution` branch, is where an assignment
# is DEFINED. It lives here rather than in `collect` because both readers need it and only
# one of them is the autograder: `collect` takes `type`/`autograde`/`tests` from it, and
# everything else in it exists to shape the grading sheet - which is this module's. The
# names `collect` still spells are re-exported there, so no caller had to move.
GRADING_FILE = "grading_config.yml"  # on the template's solution branch


# The institution's defaults, read once: the run settings of a spec nobody has resolved yet.
_INSTITUTION = settings.institution_defaults()


@dataclass(frozen=True)
class GradingSpec(_Shape):
    """One assignment's whole definition: what it is, how it is handed in, and how it is
    marked. `schedule.yml` says WHEN; this says WHAT, and the two never overlap.

    Frozen, because the read is memoised per template per process and several passes of
    one tick share it. `dropped` carries every line the parse refused - an unknown key, a
    value it could not use - in the words it printed them."""

    title: str = ""
    type: str = "individual"
    # The run settings (`settings.RUN_KEYS`) default to the institution's policy; a spec
    # built by `load_grading_spec` carries their EFFECTIVE values (`with_run_settings`).
    team_formation: str = _INSTITUTION["team_formation"]
    max_team_size: int | None = None
    submit_via: str = "assignment_repo"
    # PRIVATE unless the assignment asks otherwise (the policy's default): everything the
    # toolkit creates for a student is private to them and the teaching team, and a
    # default that published a semester's work would be a default nobody chose.
    visibility: str = _INSTITUTION["visibility"]
    # Where an `external` assignment is handed in (Moodle, Kaggle). Only the site reads it,
    # and only to put a button beside the brief.
    submit_url: str = ""
    # The starter formats, in order; the FIRST is the runnable one (see `format`).
    formats: tuple[str, ...] = ()
    questions: dict[str, str] | None = None
    # `{question: submission file}` for the questions marked from a file other than the
    # runnable format's (`questions: Q3: {points: 10, file: report.tex}`).
    question_files: dict[str, str] | None = None
    # The institution's late rule unless a nearer layer says otherwise, so an assignment
    # nobody has written a late policy for is still graded by the one the syllabi state.
    # A layer that declares ONE of the two leaves the other empty (`settings.resolve`).
    late_window_days: int | None = _INSTITUTION["late_window_days"]
    late_penalty_per_day: str | None = _INSTITUTION["late_penalty_per_day"]
    # Whether returned marks show each question's mark and feedback (a run setting).
    marks_per_question: bool = _INSTITUTION["marks_per_question"]
    # OFF unless the assignment asks for it. Most assignments are hand-marked, and a
    # default of true made every template without the key try to run hidden tests that
    # were never written - a red tick every quarter of an hour for the rest of the term.
    autograde: bool = False
    # UNDECLARED, and that is a third state rather than a missing false: the default
    # depends on the `format:` beside it (see `runs_completion_check`), so a file that
    # never mentions the key has to be told from one that turned the check off.
    completion_check: bool | None = None
    tests: str = "tests"
    # The grader's reading copy, filtered to the HAND-marked questions and archived beside
    # the autograde detail. Off unless the assignment asks for it: it clones the whole
    # semester a second time at the cutoff, and most assignments are read in the browser.
    grader_pdf: bool = False
    # How `main`'s starter is written (decision 0028): `derived` | `handwritten`, or None
    # when the file predates the key - `derive.starter_mode` then reads the markers.
    starter: str | None = None
    # The file carries a run setting (decision 0009): refused whole, and nothing hands out
    # or grades from it until it is fixed.
    not_migrated: bool = False
    dropped: tuple[str, ...] = ()
    # `{run key: layer}` - where each run setting's value came from (`with_run_settings`).
    sources: tuple[tuple[str, str], ...] = field(default=(), compare=False)

    @property
    def format(self) -> str:
        """The runnable format: the first of `formats`, or `none`. What the completion
        check and the autograder run; the other formats are there to be read."""
        return self.formats[0] if self.formats else NO_STARTER

    @property
    def is_group(self) -> bool:
        """One repo per team rather than one per student."""
        return self.type == "group"

    @property
    def team_formation_resolved(self) -> str:
        """`self_select` | `assigned` | `none` - the answer the Join-team form needs, and
        the one the lock file carries. An individual assignment has no teams to form, so
        it answers `none`: a shape nobody writes and every reader can refuse on."""
        return self.team_formation if self.is_group else NO_TEAMS

    @property
    def submit_host(self) -> str:
        """`moodle.hertie-school.org` out of `submit_url` - what the site's button is
        labelled with, so a student can see where it goes before they press it."""
        return urlsplit(self.submit_url).hostname or "" if self.submit_url else ""

    @property
    def runs_completion_check(self) -> bool:
        """Whether the cutoff executes this assignment's notebook and records whether it
        runs top to bottom (`collect`).

        ON by default for `format: ipynb` and off for everything else, because the rule it
        verifies - restart the kernel and run all before you hand in - is a rule only a
        notebook course states, and it is the one rule nobody could check by hand. An
        explicit `completion_check:` wins either way: a notebook assignment can turn it
        off, and an assignment that keeps its notebooks somewhere the `format:` does not
        name can turn it on."""
        if self.completion_check is None:
            return self.format == "ipynb"
        return self.completion_check


def _cross_check(values: dict, dropped: list[str]) -> None:
    """The settings that are only wrong BESIDE another one, refused the same way the rest
    are: the value is corrected in place and the reason is recorded.

    Each reader above sees one key and cannot see the shape it sits in, so this is where a
    combination the toolkit will not act on is caught - and it is caught at the parse, once,
    rather than by each of the handout, the sheet, the receipts and the site making their
    own guess about what was meant."""
    via = values.get("submit_via", "assignment_repo")
    if via == "shared_dropbox_repo":
        for key in ("autograde", "completion_check", "grader_pdf"):
            if values.get(key):
                # All three stages run PER UNIT against the unit's own repo, and a drop box
                # is one repo for the whole semester: each of fifty students would have the
                # whole semester's work cloned, run and archived under their own key, and
                # every one of them would get the same result. Same note beside
                # `course.SUBMIT_VIA`.
                values[key] = False
                dropped.append(
                    Dropped(
                        GRADING_FILE,
                        key,
                        f"`{key}:` is not read for a shared drop box - one repo holds "
                        f"the whole semester's work, so it is hand-marked - ignored",
                    )
                )
            elif key == "completion_check":
                # The one tri-state setting: undeclared means "whatever `format:` implies",
                # so a drop box of notebooks would switch the check on with no line in the
                # file to report as dropped. Turned off here rather than in
                # `runs_completion_check`, so the whole "is this hand-marked?" answer is
                # settled at the parse.
                values[key] = False


# What a template's `grading_config.yml` reads: every setting but the run settings, which
# are how ONE semester runs the assignment (decision 0009). A run key still written here
# is RUN_SETTING_IN_TEMPLATE.
TEMPLATE_KEYS = tuple(k for k in SPEC_KEYS if k not in settings.RUN_KEYS)


def parse_grading_spec(text: str) -> GradingSpec:
    """Parse a `grading_config.yml` into a `GradingSpec` - the TEMPLATE's view.

    A missing key falls back to the field's own default; `with_run_settings` then resolves
    the run settings through the cascade (`settings`), which is what `load_grading_spec`
    hands every reader. A malformed VALUE is logged and dropped, never raised and never
    passed through: this file is hand-edited by faculty and read by an hourly cron, so one
    bad line costs the field it sits on and nothing else."""
    data = yaml.safe_load(text) if text.strip() else {}
    if not isinstance(data, dict):
        data = {}
    # A run setting here is refused WHOLE: read without it, the assignment would run
    # on the semester's defaults instead of the rule this file wrote, and say nothing.
    for key in settings.RUN_KEYS:
        if key in data:
            raise RunSettingInTemplate(key, GRADING_FILE)
    dropped: list[str] = []
    values = read_settings(data, TEMPLATE_KEYS, GRADING_FILE, dropped)
    if values.get("questions"):
        values["question_files"] = (
            question_files(data.get("questions"), GRADING_FILE, dropped) or None
        )
    # A refused value states nothing: the field's default stands.
    values = {k: v for k, v in values.items() if v is not None}
    _cross_check(values, dropped)
    for line in dropped:
        log_err(line)
    return GradingSpec(**values, dropped=tuple(dropped))


def with_run_settings(
    spec: GradingSpec, course_org: str, semester_org: str = "", slug: str = ""
) -> GradingSpec:
    """`spec` with every run setting at its EFFECTIVE value (`settings.effective_all`) and
    `sources` saying which layer gave it. Without a semester, the semester's layers are
    empty. The shape rules hold for what the cascade brings, silently - a course default
    of `public` says nothing about an assignment that creates no repo of its own."""
    resolved = settings.effective_all(
        semester_org,
        inline=schedule.run_settings_of(semester_org, slug),
        course_org=course_org,
    )
    values = {key: value for key, (value, _) in resolved.items()}
    # The schedule entry's override of the template's shape wins (decision 0038
    # amendment 2); the template's own word otherwise.
    shape = schedule.shape_of(semester_org, slug)
    spec = replace(spec, **shape)
    if not creates_unit_repos(spec.submit_via):
        values["visibility"] = "private"
    if spec.submit_via != "external" or not values["submit_url"]:
        values["submit_url"] = ""
    return replace(
        spec,
        **values,
        sources=tuple((key, source) for key, (_, source) in resolved.items())
        + tuple((key, "assignment") for key in shape),
    )


@run.per_run(lambda course_org, template: run.scope(course_org, template))
def _grading_text(course_org: str, template: str) -> str | None:
    """The template's `grading_config.yml`, read ONCE per template per process.

    An hourly tick asks the same template the same question from the scheduler, the sheet
    refresh and the collection that follows them. Memoising the TEXT (like
    `schedule._schedule_text`) means every caller still parses its own spec - nothing
    shared to mutate - and still sees its own warnings. Held by the run (`run.per_run`)."""
    return get_file_content(course_org, template, GRADING_FILE, ref=SOLUTION_BRANCH)


def declared_grading_spec(
    course_org: str, template: str, *, semester_org: str = "", slug: str = ""
) -> GradingSpec | None:
    """`load_grading_spec`, but None when there is no definition to read at all - the
    template repo does not exist yet, or it carries no `grading_config.yml`.

    Only the lock file asks the question this way. Every other reader wants the defaults
    for an undeclared assignment and calls `load_grading_spec`; the lock file has to tell
    "declared individual" from "nobody has said yet", because the second one must lock the
    Join-team form rather than answer it."""
    try:
        text = _grading_text(course_org, template)
    except RuntimeError as exc:
        log_err(f"  ! could not read {template}/{GRADING_FILE}: {exc}")
        return None
    if text is None:
        return None
    try:
        spec = parse_grading_spec(text)
    except RunSettingInTemplate as exc:
        # Refused, and SAYS so: the lock reads it as no definition (its Join-team form
        # refuses), and the handout and the grader refuse to act on it.
        log_err(f"  ! {template}/{GRADING_FILE}: {exc} - refused")
        return GradingSpec(not_migrated=True)
    except yaml.YAMLError as exc:
        log_err(
            f"  ! {template}/{GRADING_FILE} is not valid YAML - using defaults: {exc}"
        )
        spec = GradingSpec()
    return with_run_settings(spec, course_org, semester_org, slug)


def load_grading_spec(
    course_org: str, template: str, *, semester_org: str = "", slug: str = ""
) -> GradingSpec:
    """The assignment's definition from the course template's `solution` branch, with its
    run settings resolved for `slug` (the schedule key) of `semester_org`.

    A template with no solution branch, no definition file, or one that does not parse
    leaves the rest of the tick running on the defaults rather than taking the semester
    down with it. A failed read of a CASCADE layer (the course's `course.yml`, the
    semester's `semester.yml`) does raise (`settings.course_defaults`): those decide
    what the defaults are."""
    spec = declared_grading_spec(
        course_org, template, semester_org=semester_org, slug=slug
    )
    if spec is None:
        spec = with_run_settings(GradingSpec(), course_org, semester_org, slug)
    return spec


# ------------------------------------ what an assignment's definition will not grade as
#
# TIME-BOUND, unlike everything else that reaches a digest. `submit_via: emial` is a typo
# in a file, and until the assignment is graded it is a typo that costs nothing: the fix
# is the same in August and on the morning of the deadline. So it keeps the SOURCE clock -
# it climbs the ladder as the grading moment approaches - rather than shouting from the
# day somebody saved it.


def _spec_fault(
    slug: str,
    template: str,
    course_org: str,
    fires: datetime | None,
    what: str,
    field: str = "",
    lineno: int | None = None,
    fix: str = "",
    file: str = GRADING_FILE,
    plain: str = "",
    consequence: str = "",
    per_semester: bool = False,
    code: str = "",
) -> ConfigFault:
    """One value in one assignment's definition that will not grade as written.

    The fault is in the COURSE org, on the template's `solution` branch, and the digest
    that carries it is the SEMESTER's - the semester is what the grading happens to, and the
    people who can fix it are the ones in its semester.yml. So the file's own address rides
    on the fault (`in_org`, `in_repo`, `ref`), which is what the deep link and the blame
    query both go by."""
    return ConfigFault(
        f"assignments.{slug}",
        what,
        fires=fires,
        field=field,
        lineno=lineno,
        file=file,
        in_repo=template,
        in_org=course_org,
        ref=SOLUTION_BRANCH,
        fix_text=fix,
        plain=plain,
        consequence=consequence,
        per_semester=per_semester,
        code=code,
    )


def grading_spec_faults(
    slug: str,
    template: str,
    course_org: str,
    text: str,
    fires: datetime | None,
    handed_out: list[dict] | None = None,
    releases_solution: bool = False,
    semester_org: str = "",
) -> tuple[list[ConfigFault], GradingSpec | None]:
    """Everything in ONE `grading_config.yml` the parse had to refuse, as faults - and the
    spec that parse produced, so the caller does not read and parse the same file again.
    `None` in its place is a file that is not YAML at all; the faults say so.

    Handing the spec back is not a convenience: `parse_grading_spec` LOGS every line it
    drops, so a second parse of the same file printed every one of them twice in the same
    digest run.

    The parse's own sentences, in the words it already logs them in: it is the one place
    that knows the vocabulary each key accepts, and a fault that paraphrased it would be a
    second, slightly different answer about what is allowed.

    `handed_out` is the repos this assignment actually created, off the caller's listing:
    one more fault, off the SAME parse, because parsing a second time here would log every
    refused line in this file twice. None means there is nothing to compare the file
    against - the assignment has not gone out, or the listing could not be read.

    `releases_solution` is whether this assignment's `schedule.yml` entry carries a
    `solution_datetime:` - the one fact about it that the definition here can contradict
    (see below), and the only thing read from outside this file.

    The checks against the handed-out repos and the solution release read the RESOLVED
    spec (`with_run_settings` for `slug` of `semester_org`), the one the handout acts on:
    a visibility set on the schedule entry, in `semester.yml` or the course's defaults is what the repos were
    created with, and the template alone would give the digest a second answer."""
    try:
        spec = parse_grading_spec(text)
    except RunSettingInTemplate as exc:
        return [
            _spec_fault(
                slug,
                template,
                course_org,
                fires,
                f"{exc} - the whole file is refused, so the assignment is not handed "
                f"out or graded until it is",
                field=exc.old,
                lineno=key_lines(text).get((exc.old,)),
                fix=f"move `{exc.old}:` to the assignment's entry in schedule.yml",
                code=exc.code,
            )
        ], None
    except yaml.YAMLError as exc:
        return [
            _spec_fault(
                slug,
                template,
                course_org,
                fires,
                "this file is not valid YAML, so none of it is read and the whole "
                "assignment grades on the toolkit's defaults",
                lineno=yaml_mark_line(exc),
                fix="fix the YAML on the line above",
                plain=f"The {slug} template's settings file is not valid YAML, so "
                f"the assignment is marked on the toolkit's defaults.",
            )
        ], None
    spec = with_run_settings(spec, course_org, semester_org, slug)
    lines = key_lines(text)
    faults = [
        _spec_fault(
            slug,
            template,
            course_org,
            fires,
            dropped.what,
            field=dropped.field,
            lineno=lines.get((dropped.field,)),
            fix=_spec_fix(dropped),
            code=dropped.code,
        )
        for dropped in spec.dropped
        if isinstance(dropped, Dropped)
    ]
    if handed_out:
        faults += _visibility_faults(spec, slug, semester_org, fires, handed_out)
    if releases_solution and not spec.can_hold_solution:
        # A moment that will pass and do nothing. `provision_all` refuses to push the
        # model answer where there is no repo of the unit's own to put it in, or none the
        # toolkit can promise is private - and the two files are written by different
        # people, so nothing else would notice that the plan schedules a release the
        # definition forbids. ONE predicate, asked in both places
        # (`course.can_hold_solution`).
        faults.append(
            _spec_fault(
                slug,
                template,
                course_org,
                fires,
                f"this assignment's entry in `schedule.yml` carries a "
                f"`solution_datetime:`, but `submit_via: {spec.submit_via}` with "
                f"`visibility: {spec.visibility}` gives it no private repo of its own to "
                f"push the model solution into - it is never pushed where the world can "
                f"read it, where the students can publish it, or where the whole semester "
                f"shares one repo, so that moment passes and nothing is released",
                field="visibility",
                lineno=lines.get(("visibility",)),
                fix="remove `solution_datetime:` from this assignment's entry in "
                f"{CONFIG_REPO}/schedule.yml - the model answer stays on this "
                "template's `solution` branch, which is where the instructors read "
                "it - or give this assignment a private repo per unit "
                f"(`submit_via: assignment_repo` here, `assignment_repo_visibility: "
                f"private` on its entry in {CONFIG_REPO}/schedule.yml)",
                plain=f"{slug} has a solution shown date in this semester's schedule, "
                f"but its settings give it no private repo to put the solution in.",
                consequence="the solution shown date passes and no solution is "
                "released",
                per_semester=True,
            )
        )
    return faults, spec


def _spec_fix(dropped: Dropped) -> str:
    """What would put one refused line right: the vocabulary the key accepts, or - for a
    key the toolkit has no reader for at all - the keys it does read.

    "Correct the value" is not an instruction about a line whose KEY is the mistake: a
    setting that moved here from schedule.yml, or a plain misspelling, has no value to
    correct."""
    if dropped.field not in SPEC_KEYS:
        return f"remove the line above, or spell it as one of: {', '.join(SPEC_KEYS)}"
    allowed = f" (allowed: {'/'.join(dropped.allowed)})" if dropped.allowed else ""
    return f"correct the value on the line above{allowed}"


def grading_config_faults(
    course_org: str,
    semester_org: str,
    sched,
    found: list[ConfigFault],
    listing: dict[str, dict] | None,
) -> None:
    """Every assignment this semester's plan declares, and everything in its definition that
    will not grade as written.

    `fires` is the moment the value is USED: the assignment's late cutoff
    (`schedule.grading_cutoff_datetime`).

    `listing` is the SEMESTER's repos keyed by name, off the one listing the tick already
    holds, and it is read for one check: an assignment whose `visibility:` no longer
    describes the repos it handed out (see `_visibility_faults`). None is "we could not
    look", and that check reports nothing - it is not worth dropping a whole file's digest
    for, since every other fault in it was read from the template.

    `semester_org` is read for one more thing, and only where a definition asks for it: an
    assignment that hands the visibility flag to its students depends on two settings of
    the ORG, which cannot be set through the API at all (`_org_settings_faults`). One GET
    for the whole plan, and none at all for a semester with no such assignment.

    Everything else here is the definition alone, which lives in the course org. Nothing
    is appended until every template has been read: a read that failed is "we could not
    look", and the digest closes what it is not handed."""
    faults: list[ConfigFault] = []
    # Whether any assignment under this plan has handed the visibility flag to its
    # students, which is what makes the org's own two switches worth an API read - see
    # `_org_settings_faults`.
    hands_the_flag_over = False
    for slug, entry in sorted(sched.assignments.items()):
        template = entry.course_source_repo
        if not template:
            continue  # the plan itself is faulty; schedule.yml's own digest says so
        # The window-less cutoff: the spec that holds the window is what is read next.
        fires = schedule.grading_cutoff_datetime(sched, slug)
        text = _grading_text(course_org, template)
        if text is None:
            continue  # declares nothing: graded as an individual, hand-marked one
        # Nothing to compare a file against until the assignment has gone out. Archived
        # repos are left out: one is read-only and a finished semester is meant to stay
        # frozen, so nothing it says can be anybody's fault.
        handed_out = None
        if listing is not None and entry.handout_datetime is not None:
            handed_out = assignment_rows(listing, schedule.semester_name(slug, entry))
        spec_faults, spec = grading_spec_faults(
            slug,
            template,
            course_org,
            text,
            fires,
            handed_out,
            releases_solution=entry.solution_datetime is not None,
            semester_org=semester_org,
        )
        faults += spec_faults
        if handed_out and spec is not None and spec.visibility_is_students:
            hands_the_flag_over = True
    # ONE read of the org, after every template has been parsed and only when something
    # in this semester actually depends on it: an org with no such assignment is not
    # misconfigured, it is an org the question does not apply to.
    if hands_the_flag_over:
        faults += _org_settings_faults(semester_org)
    found.extend(faults)


def _visibility_faults(
    spec: GradingSpec,
    slug: str,
    semester_org: str,
    fires: datetime | None,
    rows: list[dict],
) -> list[ConfigFault]:
    """`visibility:` against the repos this assignment actually handed out, `rows`.

    The value is read at CREATE and nowhere else (see `repos.set_visibility`), so editing
    the line afterwards is a silent no-op: the file says `public`, the semester's work stays
    private, and every page the toolkit writes describes repos that do not exist. Nothing
    else notices, which is why this is a fault and not a log line.

    The listing's word against the file's, compared as both are written - they are the same
    vocabulary. A shape whose repos are legitimately a MIXTURE, because the students own
    the flag, is exempted by its own predicate in `course.py`, never by a name spelt
    here. `creates_repos` rather than `creates_unit_repos`: a shared drop box is one repo
    for the whole semester and `visibility:` describes it exactly as it describes the many."""
    if not spec.creates_repos or spec.visibility_is_students:
        # `student_choice` says the STUDENT decides, so twenty private repos and four
        # public ones is the assignment working - `course.visibility_is_students`. The
        # thing that CAN be wrong about it is the org, not the file: see
        # `_org_settings_faults`.
        return []
    wrong = [r for r in rows if r.get("visibility") != spec.visibility]
    if not wrong:
        return []
    # The semester's own run settings (its schedule.yml entry, else semester.yml
    # `defaults:`, decision 0039), in the semester's settings digest: free to change until
    # the first hand out, and a drift report afterwards.
    return [
        ConfigFault(
            f"assignments.{slug}",
            # A COUNT and never a name: a submission repo is `<slug>-<handle>`, and this
            # sentence is repeated into a public run log, a digest issue and an email.
            f"`assignment_repo_visibility: {spec.visibility}` does not describe the repos this assignment "
            f"handed out - {len(wrong)} of {len(rows)} are not {spec.visibility}. The "
            f"value is read when each repo is CREATED, so editing it afterwards moves "
            f"nothing on its own",
            fires=fires,
            field="assignment_repo_visibility",
            file=SEMESTER_FILE,
            in_org=semester_org,
            fix_text=f"set this assignment's `assignment_repo_visibility:` (on its "
            f"schedule.yml entry, else in {SEMESTER_FILE} `defaults:`) back to "
            f"what those repos are, or make each of them {spec.visibility} by hand from "
            f"its GitHub Settings - the toolkit never re-opens a repo it has already "
            f"created",
            plain=f"{slug}'s settings say its repos are {spec.visibility}, but "
            f"{len(wrong)} of {len(rows)} handed out in this semester are not.",
            consequence="those repos stay as they are, and the pages the toolkit "
            "writes describe them wrongly",
            per_semester=True,
        )
    ]


# WHERE the org-settings fault sits in the digest's state. ONE key for the whole semester
# and not one per assignment: the two switches belong to the org, so three `student_choice`
# assignments under one plan are three readings of one problem, and three keys would mail
# about it three times and clear it three times.
ORG_SETTINGS = "org settings"
# The page both switches are on. Web-only: the API can read them and cannot set them.
MEMBER_PRIVILEGES_URL = (
    "https://github.com/organizations/{org}/settings/member_privileges"
)


def _org_settings_faults(semester_org: str) -> list[ConfigFault]:
    """The org's own two switches, against what `visibility: student_choice` needs of them.

    That shape gives the student `admin` on their own repo, because `admin` is the only
    permission carrying GitHub's visibility control. It carries other things too, and the
    org is the only place they can be taken back - so the shape is safe exactly where
    members may change a repo's visibility and may NOT delete or transfer one.

    READ-only, both of them: they are reported by `GET /orgs/{org}` and absent from
    `PATCH /orgs/{org}` (they are web-only settings), so the maintainer sets them once per
    semester org and this is what notices when nobody did. Explicit `is True` / `is False`,
    never truthiness: a plan whose payload omits a key has said nothing about it, and
    faulting on a missing field would red every semester on an account tier that does not
    carry it."""
    settings = gh_teams.org_settings(semester_org)
    if settings is None:
        return []  # we could not look; `org_settings` has already said why
    wrong: list[str] = []
    costs: list[str] = []
    if settings.get(gh_teams.MEMBERS_CAN_DELETE) is True:
        wrong.append(
            "**Allow members to delete or transfer repositories** is ON, so a student "
            "can delete or move their own submission"
        )
        costs.append("a student can delete or move their own submission")
    if settings.get(gh_teams.MEMBERS_CAN_PUBLISH) is False:
        wrong.append(
            "**Allow members to change repository visibilities** is OFF, so no student "
            "can publish their work and the shape does nothing for them"
        )
        costs.append("no student can publish their work")
    if not wrong:
        return []
    return [
        ConfigFault(
            ORG_SETTINGS,
            f"an assignment in this semester is handed out with "
            f"`visibility: student_choice`, which makes each student an admin of their "
            f"own repo - and {' and '.join(wrong)}",
            file=GRADING_FILE,
            in_org=semester_org,
            plain="This semester's GitHub member privileges do not suit an assignment "
            "that lets students choose their repo's visibility.",
            consequence=" and ".join(costs),
            per_semester=True,
            # Both switches named whichever one is wrong: the fix is one visit to one
            # page, and a sentence that named only the offender would send somebody back
            # there a second time for the other.
            fix_text=(
                f"on the semester org's Member privileges page "
                f"({MEMBER_PRIVILEGES_URL.format(org=semester_org)}) set **Allow members to change repository "
                f"visibilities** ON and **Allow members to delete or transfer "
                f"repositories** OFF. Both are web-only org settings - the toolkit reads "
                f"them and cannot set them - and they are the one-time semester-org step "
                f"in docs/DEPLOYMENT-CHECKLIST.md"
            ),
        )
    ]


def _display_moment(at: datetime | None) -> str:
    """`Sun 4 Oct 2026 23:59` - a date a grader reads, not one a machine parses. Built by
    hand rather than with `%-d`, which is a glibc/BSD extension."""
    if at is None:
        return ""
    return f"{at:%a} {at.day} {at:%b} {at.year} {at:%H:%M}"


def _display_long(at: datetime | None, tz_name: str = "") -> str:
    """`Sunday 4 October 2026, 23:59 (Europe/Berlin)` - the form a STUDENT reads, once, in
    an issue they have to act on. The sheet's header uses the short form beside it: a
    grader scans that file rather than reading it."""
    if at is None:
        return ""
    zone = f" ({tz_name})" if tz_name else ""
    return f"{at:%A} {at.day} {at:%B %Y}, {at:%H:%M}{zone}"


@run.per_run(lambda course_org, template: run.scope(course_org, template))
def readme_heading(course_org: str, template: str) -> str:
    """The template README's first `# ` heading, or "" - the name an assignment has when
    its `grading_config.yml` gives no `title:`. Read once per template per process; a
    read that fails is no heading. Held by the run (`run.per_run`)."""
    if not course_org or not template:
        return ""
    try:
        text = get_file_content(course_org, template, "README.md") or ""
    except RuntimeError:
        return ""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def assignment_title(
    course_org: str, template: str, spec: GradingSpec, slug: str
) -> str:
    """What an assignment is called on a sheet, in status and in mail: the template's
    `title:`, else its README heading, else the slug. A `title:` that only repeats the
    slug's identifier (the seeded placeholder `Assignment N`, `course.row_name`) names
    nothing, so the heading takes over, as on the site and the student console."""
    if row_name(spec.title, identifier(slug)):
        return spec.title
    return readme_heading(course_org, template) or spec.title or slug


def sheet_spec(
    sched: schedule.Schedule,
    key: str,
    slug: str,
    gspec: GradingSpec,
    is_group: bool,
    title: str = "",
) -> SheetSpec:
    """What the sheet needs to know about this assignment, gathered from the two files
    that own it: `grading_config.yml` on the template's solution branch, and the semester's
    `schedule.yml`. Nothing here is written into the sheet as data - it reaches the grader
    as the comment header, which is regenerated on every write."""
    entry = sched.assignments.get(key)
    return SheetSpec(
        slug=slug,
        title=title or gspec.title or slug,
        is_group=is_group,
        submit_via=gspec.submit_via,
        visibility=gspec.visibility,
        questions=gspec.questions,
        question_files=gspec.question_files,
        late_window_days=gspec.late_window_days,
        late_penalty_per_day=gspec.late_penalty_per_day,
        marks_per_question=gspec.marks_per_question,
        autograde=gspec.autograde,
        completion_check=gspec.runs_completion_check,
        due_display=_display_moment(entry.due_datetime if entry else None),
        cutoff_display=_display_moment(schedule.grading_cutoff_datetime(sched, key)),
        due_long=_display_long(entry.due_datetime if entry else None, sched.timezone),
        cutoff_long=_display_long(
            schedule.grading_cutoff_datetime(sched, key),
            sched.timezone,
        ),
        due_at=entry.due_datetime if entry else None,
        not_migrated=gspec.not_migrated,
    )


def total_points(spec: SheetSpec | GradingSpec) -> str:
    """The assignment's total, or "" when the maxima are not all numbers - `questions`
    holds them as written, and a course may declare `Q1: see rubric`.

    Public, and takes either spec, because the semester site prints the same total on the
    assignment's page (`site._assignment_entry`) that the gradebook prints beside a score.
    One implementation: a second sum of the same maxima is a second answer to "what is
    this assignment out of", and the two would part company the first time one was
    changed."""
    maxima = [as_decimal(maximum) for maximum in (spec.questions or {}).values()]
    if not maxima or None in maxima:
        return ""
    return _plain(sum(maxima, Decimal(0)))


def semester_sheet_faults(
    course_org: str, semester_org: str, sched, found: list[ConfigFault]
) -> None:
    """Every grading sheet this semester's plan declares, and everything in each of them a
    grader has to settle.

    Asked on the scheduled tick and NOWHERE else - no push fast path. A sheet is edited
    all day while marking, so a notification per save would mail a grader about a file
    they are still typing into; the tick catches it once it has been left that way.

    Nothing is appended until every sheet has been READ. Half a list is worse than none:
    the digest closes what it is not handed, so a rate limit part-way through would report
    the sheets it never reached as repaired. A read that failed raises, and the caller
    drops the sheets from this tick (see `scheduler._config_faults`)."""
    specs = sheet_specs(course_org, sched)
    faults: list[ConfigFault] = []
    for name in sorted(specs):
        text = get_file_content(semester_org, CONFIG_REPO, sheet_path(name))
        if text is None:
            continue  # no sheet yet - the normal state before an assignment is due
        faults += sheet_faults(name, text, specs[name])
    found.extend(faults)


def sheet_specs(course_org: str, sched) -> dict[str, SheetSpec]:
    """One spec per assignment the semester's schedule declares, keyed by its SEMESTER-side
    name - which is what the sheets, the repos and the gradebooks are all named after."""
    specs: dict[str, SheetSpec] = {}
    for key, entry in sched.assignments.items():
        name = schedule.semester_name(key, entry)
        gspec = (
            load_grading_spec(
                course_org, entry.course_source_repo, semester_org=sched.org, slug=key
            )
            if course_org
            else GradingSpec()
        )
        specs[name] = sheet_spec(
            sched,
            key,
            name,
            gspec,
            gspec.is_group,
            assignment_title(course_org, entry.course_source_repo, gspec, name),
        )
    return specs
