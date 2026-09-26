"""Repositories themselves: does one exist, what is its default branch, create it,
topic and describe it, add a collaborator - and what a published page may never carry.
"""

from __future__ import annotations

import json
import time
from fnmatch import fnmatch
from functools import cache
from typing import NamedTuple

from .course import RETIRED_REPO_NAMES
from .faults import NOT_MIGRATED
from .ghcli import ALL, FILES, gh, is_already_exists, is_missing_resource, on_write
from .log import log, log_err, log_err_person, log_ok, log_person, log_skip


class _RepoReadFailed(RuntimeError):
    """`GET repos/{org}/{name}` did not answer. Carries gh's output, so a caller can ask
    `is_missing_resource` whether that was a definite 404 or merely "could not tell"."""

    def __init__(self, out: str) -> None:
        super().__init__(out)
        self.out = out


@cache
def _repo(org: str, name: str) -> dict:
    """The repo object, read once per repo per process.

    "Is it there", "is it private", "is it archived", "what is its default branch" are
    four questions about ONE object, and a single sweep asks several of them about the
    same repo; a repo's identity cannot change under one run. A failed read RAISES rather
    than returning a sentinel, because functools.cache does not memoise a raise - so a 502
    is retried on the next question instead of being pinned for the life of the process.
    Cleared between tests (tests/conftest.py)."""
    code, out = gh("api", f"repos/{org}/{name}")
    if code != 0:
        raise _RepoReadFailed(out)
    try:
        body = json.loads(out)
    except json.JSONDecodeError as exc:
        raise _RepoReadFailed(out) from exc
    if not isinstance(body, dict):
        raise _RepoReadFailed(out)
    return body


def _forget_repo(kind: str, targets: frozenset[str]) -> None:
    """A write that makes, renames, archives or deletes a repo (an org-wide target, see
    `ghcli.written`) makes `_repo`'s answers stale. Rare, so the whole memo goes."""
    if kind in (FILES, ALL) and any("/" not in t for t in targets):
        _repo.cache_clear()


on_write(_forget_repo)


def repo_missing(org: str, name: str) -> bool:
    """Whether GitHub positively says the repo is NOT there (a 404). The shape for a
    caller about to record something permanent on the strength of absence: a 5xx or a
    rate limit is neither present nor absent, and must read as "could not tell"."""
    try:
        _repo(org, name)
    except _RepoReadFailed as exc:
        return is_missing_resource(exc.out)
    return False


def repo_exists(org: str, name: str) -> bool:
    """Whether the repo is there. OPTIMISTIC: any read failure reads as absent, because
    this answers a create-if-missing question where guessing wrong costs a retry.

    Its neighbour `org_exists` is deliberately the opposite shape - it raises rather than
    call an unreadable org deleted - because its callers act destructively on a False.
    Reach for that one whenever absence is going to remove something."""
    try:
        _repo(org, name)
    except _RepoReadFailed:
        return False
    return True


def org_exists(org: str) -> bool:
    """Whether `org` is still a live GitHub org.

    Fails CLOSED: only an unambiguous 404 is absence. A 403, a 5xx, a rate limit or a
    timeout all mean "could not tell", and both callers act destructively on a False (a
    row dropped from a generated page, a semester unregistered from every nightly sync), so
    it raises instead. `repo_exists` above is deliberately the opposite shape.

    Even the 404 is weaker evidence than it looks: GitHub answers 404, not 403, for an org
    the TOKEN cannot see, so a bot removed from one org reads exactly like a deleted one.
    False therefore means "not visible to this token", and seed._live_semesters requires two
    consecutive misses before acting on it."""
    code, out = gh("api", f"orgs/{org}", "--jq", ".login")
    if code == 0:
        return True
    if is_missing_resource(out):
        return False
    raise RuntimeError(
        f"could not determine whether the org `{org}` still exists: {out[:200]}"
    )


def repo_is_private(org: str, name: str) -> bool:
    """Return True if the repo is private (assume private if the check fails)."""
    try:
        return bool(_repo(org, name).get("private", True))
    except _RepoReadFailed:
        return True


def listed_is_private(row: dict | None) -> bool:
    """Whether a row of an org listing describes a repo NOBODY outside its collaborators
    can read.

    Deliberately not the same answer as `repo_is_private`, which reads the boolean
    `private` - GitHub sets that on an `internal` repo too, and an `internal` repo is
    readable by every member of the enterprise. This reads `visibility`, so `internal`
    answers False. That is the answer its caller needs: what rides on it is whether a
    student's submission receipts may be posted into a repo's issue, and a hand-in time
    read by the whole institution is a published fact about that student. `internal` is not a visibility the toolkit
    hands out (`course.VISIBILITIES`); when it is, this still will not treat it as private.

    A row that is not there at all answers private, so a listing that could not be read
    never turns into a repo treated as world-readable."""
    return ((row or {}).get("visibility") or "private") == "private"


def repo_is_archived(org: str, name: str) -> bool:
    """Return True if the repo is archived (assume LIVE if the check fails).

    Archived repos are read-only - every write 403s. The optimistic default is deliberate:
    a transient API failure must not silently skip a live semester's refresh. Guess wrong
    that way and the write itself fails loudly, which is the outcome we want.
    """
    try:
        return bool(_repo(org, name).get("archived"))
    except _RepoReadFailed:
        return False


# GitHub's answers while a repo is still settling. A repo is BUSY for a few seconds after
# it is created, and LOCKED for rather longer after its visibility is flipped; in either
# window the next write is refused with a 409 or a 422 carrying one of these.
_SETTLING = (
    "operation is still in progress",
    "locked and cannot be modified",
    "conflicting repository operation",
)
# Twelve tries, five seconds apart: a ~60 s window. The lock after a flip outlasted the 8 s
# a create's own settling took (both seen live, 2026-09-17).
_SETTLE_ATTEMPTS = 12
_SETTLE_DELAY = 5.0


def _is_settling(out: str) -> bool:
    """Whether gh's refusal is GitHub saying "this repo is busy, come back in a moment"."""
    folded = out.lower()
    return any(marker in folded for marker in _SETTLING)


def gh_settled(*args: str, **kwargs) -> tuple[int, str]:
    """`gh`, retried while GitHub says the repo is still settling - and ONLY then.

    Creating a repo and flipping its visibility both lock it, and the next write is refused
    outright rather than queued: seen live on 2026-09-17, a visibility PATCH one second
    after `generate` ("a previous repository operation is still in progress"), then the
    team grant, the collaborator grant and the topics PUT one second after that flip ("this
    repository is locked and cannot be modified"). A handout does all of those in a row, so
    one waits for the other. Every write that can land on a just-created or just-flipped
    repo goes through this; reads do not, and no other refusal is retried - a 403 is a
    scope problem that waiting cannot fix.
    """
    code, out = gh(*args, **kwargs)
    for _ in range(_SETTLE_ATTEMPTS - 1):
        if code == 0 or not _is_settling(out):
            break
        time.sleep(_SETTLE_DELAY)
        code, out = gh(*args, **kwargs)
    return code, out


def archive_repo(org: str, name: str, *, person: bool = False) -> bool:
    """Archive a repo - GitHub's reversible read-only freeze. Idempotent (an archived repo
    re-archives fine).

    The strongest thing this toolkit can do to a repo, deliberately: the bot's token holds
    no `delete_repo` scope, so a finished semester is CLOSED rather than destroyed, and a repo
    frozen in error is un-archived from its own Settings page with nothing lost.

    An archived repo takes no push, no issue and no collaborator change, so anything a
    caller still means to READ or WRITE has to happen BEFORE this lands - see
    `dsl_course.teardown`, whose whole order follows from that. Freezing is also how it
    withdraws write access: read-only for everyone is what a closed semester is, so nobody
    is revoked and everyone keeps the read they had.

    `person=True` when the repo is somebody's, so the failure line names it only in the
    verbose log (see `log.log_err_person`)."""
    code, out = gh_settled(
        "api", "--method", "PATCH", f"repos/{org}/{name}", "--field", "archived=true"
    )
    if code == 0:
        return True
    _failed_on(
        person,
        f"could not archive a repo in {org}",
        f"could not archive {org}/{name}: {out[:160]}",
    )
    return False


def set_visibility(
    org: str, name: str, visibility: str, *, person: bool = False
) -> bool:
    """Make an existing repo `private` or `public`. Idempotent (GitHub accepts the
    visibility a repo already has).

    A PATCH of its own because `POST /repos/{o}/{r}/generate` takes `private` and nothing
    else: a repo generated from a template is born private, and an assignment whose
    `visibility:` says `public` is that repo flipped immediately afterwards. So the flip
    belongs to the CREATE path - re-PATCHing a repo that already exists would undo, on
    every quarter-hourly tick, whatever a person had deliberately changed.

    The ONE exception is the shape where a person changing it is the thing being
    corrected: a `student_choice` repo published before its grading cutoff is put back
    here by `scheduler._reprivatise_student_repos`, which is the promise the assignment's
    own page makes to the rest of the semester. After the cutoff nothing flips it again.

    `person=True` when the repo is somebody's, so the failure line names it only in the
    verbose log (see `log.log_err_person`)."""
    # A repo generated from a template is still being populated for a few seconds, and a
    # PATCH in that window is refused - so this is one of the writes that waits out a
    # settling repo (`gh_settled`).
    code, out = gh_settled(
        "api",
        "--method",
        "PATCH",
        f"repos/{org}/{name}",
        "--field",
        f"visibility={visibility}",
    )
    if code == 0:
        return True
    _failed_on(
        person,
        f"could not set a repo's visibility in {org}",
        f"could not make {org}/{name} {visibility}: {out[:160]}",
    )
    return False


def allow_forking(org: str, name: str) -> bool:
    """Let a private repo be forked. Idempotent, and a NO-OP unless there is work to do.

    TWO settings stand between a student and the Fork button on a private repo, and both
    are off by default: the org's (`gh_teams.converge_org_settings`) and this one, per
    repo. `create_repo`'s POST takes no forking field, so it is a PATCH of its own, run on
    every release rather than only at creation - a repo made before this existed has to
    converge too, and the release is the only thing that visits one regularly.

    Which is why it READS before it writes. The release runs every quarter of an hour, so
    an unconditional PATCH is 96 writes a day per dest for a flag that changes once; and
    GitHub REFUSES the field on a public repo ("Allow forks setting can only be changed on
    org-owned private repositories", HTTP 422), so a semester whose materials repo is public
    logged a warning on every tick, for ever, about a repo that anyone can already fork.
    The read costs nothing: `repo_is_archived` has fetched this same repo object earlier
    in the same run and `_repo` is cached per process.

    Fail-open: a repo that cannot be read is PATCHed as before, and a refusal is worth a
    line and NOT an error - on some plans the setting is not available at all, and
    reddening a quarter-hourly release for a button is how a real failure stops being
    noticed."""
    try:
        repo = _repo(org, name)
    except _RepoReadFailed:
        pass
    else:
        # Public: forkable by nature, and the PATCH would 422. Already true: nothing to do.
        if not repo.get("private", True) or repo.get("allow_forking"):
            return True
    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"repos/{org}/{name}",
        "--field",
        "allow_forking=true",
    )
    if code == 0:
        return True
    log(f"  [warn] {org}/{name} could not be made forkable: {out[:120]}")
    return False


# The repository ruleset a shared drop box is handed out with, named so the GET below
# can recognise our own and leave anything a maintainer added alone. A RULESET and not
# classic branch protection: rulesets apply to private repositories on the Free plan,
# where classic protection does not - and the drop box is private by definition.
DROP_BOX_RULESET = "dsl-drop-box"


# GitHub's refusal when a plan feature is asked of a private repo on Free; the same
# sentence for rulesets, required reviewers and protected branches.
_PLAN_REFUSAL = "upgrade to github pro or make this repository public"


def plan_refused_rulesets(out: str) -> bool:
    """Whether a rulesets call was refused because the org's PLAN lacks the feature."""
    return _PLAN_REFUSAL in out.lower()


def protect_shared_repo(org: str, name: str) -> bool:
    """Stop the default branch of a shared drop box being force-pushed or deleted.

    Every student in the semester has `push` on that one repo, so without this ONE of them
    can erase the whole semester's work and the history it is pinned to - and the deadline
    snapshot then points at a commit that no longer exists. Folders are a convention
    inside the repo, not a boundary, and nothing GitHub offers makes them one; what CAN be
    guaranteed is that whatever was pushed stays reachable, which is what these two rules
    buy. `git log` keeps the rest.

    `non_fast_forward` + `deletion` and nothing else: a rule requiring pull requests, or a
    linear history, would stop the ordinary push the assignment is handed out to collect.
    No bypass actors - an org owner can still edit the ruleset itself, which is the escape
    hatch, and listing one would only widen who may rewrite the semester's work.

    Idempotent, and reads before it writes: the handout re-fires on every tick, so a
    second POST would 422 on the name for the rest of the term. A listing that could not
    be read falls through to the POST and, if that is refused, counts as a failed handout
    - the next tick reads the listing again and skips, so it heals itself."""
    code, out = gh(
        "api",
        "--paginate",
        f"repos/{org}/{name}/rulesets?per_page=100",
        "--jq",
        ".[].name",
    )
    if code == 0 and DROP_BOX_RULESET in {ln.strip() for ln in out.splitlines()}:
        log_skip(f"{org}/{name} ruleset {DROP_BOX_RULESET}")
        return True
    code, out = gh_settled(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{name}/rulesets",
        "--input",
        "-",
        stdin=json.dumps(
            {
                "name": DROP_BOX_RULESET,
                "target": "branch",
                "enforcement": "active",
                "bypass_actors": [],
                # `~DEFAULT_BRANCH` rather than `main`: the drop box is generated from the
                # semester template, so its default branch is whatever the course template's
                # is, and a ruleset naming the wrong branch protects nothing.
                "conditions": {
                    "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
                },
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
            }
        ),
    )
    if code == 0:
        log_ok(f"{org}/{name} cannot be force-pushed or deleted")
        return True
    if plan_refused_rulesets(out):
        # Rulesets on a PRIVATE repo are a GitHub Team feature; every Hertie org is on Free
        # until the Education upgrade lands. Not a failed handout: the drop box works, it is
        # merely unprotected, and the next tick's POST succeeds the day the plan changes.
        log_err(
            f"{org}/{name} is NOT protected against force-push: rulesets on a private repo "
            f"need GitHub Team (the org is on Free). Every student in the semester has push, "
            f"so a force-push could rewrite the semester's work - git history is the only "
            f"safety net until the plan is upgraded."
        )
        return True
    log_err(
        f"could not protect {org}/{name} against force-push and deletion: {out[:160]}. "
        f"Every student in the semester has push on that repo, so until the ruleset is "
        f"there one of them can erase the whole semester's work - add it by hand from "
        f"Settings > Rules, or re-run the release."
    )
    return False


def set_default_branch(org: str, name: str, branch: str) -> bool:
    """Point the repo's HEAD at `branch`. Idempotent (naming the branch it already opens
    on is accepted).

    `create_repo`'s POST carries no default_branch field, and a push does not move HEAD -
    so a repo whose branches all arrived by push opens on whichever one GitHub guessed
    from them. Nothing else in the toolkit has to say it: the release and propagate commit
    onto whatever `default_branch` reports. This exists for the copy paths, which build a
    repo out of another repo's branches and have to carry that repo's default over with
    them."""
    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"repos/{org}/{name}",
        "--field",
        f"default_branch={branch}",
    )
    if code == 0:
        return True
    log_err(f"could not open {org}/{name} on {branch}: {out[:160]}")
    return False


def default_branch(org: str, name: str, *, fallback: str | None = None) -> str:
    """The repo's default branch.

    Fail-LOUD by default, which is what a writer needs: guessing "main" aims a commit at a
    branch that may not be the one that exists, so put_files would rather fail than land
    work somewhere nobody is looking. A READER that would otherwise just find nothing
    passes `fallback="main"` and gets it whenever the repo cannot be read."""
    detail = "the repo names no default branch"
    try:
        branch = str(_repo(org, name).get("default_branch") or "").strip()
        if branch:
            return branch
    except _RepoReadFailed as exc:
        detail = exc.out[:200]
    if fallback is not None:
        return fallback
    raise RuntimeError(f"could not read {org}/{name}'s default branch: {detail}")


SUPERSEDED_DESCRIPTIONS = {
    # Claimed "enrolled students only", but grant_read_teams gives the `auditors` team read
    # on every released repo too - so the repo table students land on carried a false claim
    # about who can see the materials.
    "Released course materials (enrolled students only)": (
        "Released lectures, labs, readings, & other materials"
    ),
    # The wording that replaced the one above, superseded in its turn. A chain, not a
    # rewrite: an org still on the oldest string has to reach the newest in one pass, so
    # every link keeps pointing at the CURRENT text rather than at its immediate successor.
    "Released lectures, labs, readings, and other materials": (
        "Released lectures, labs, readings, & other materials"
    ),
    "Course materials (lectures/readings by session)": (
        "Course materials (lectures/labs/readings/datasets/other) by session"
    ),
    # The site repo is generated and rewritten on every sync (site.py stamps that inside the
    # repo itself), so its description says so where faculty see it: on the org's landing
    # page, beside the repos they SHOULD open. "on push" went with it - true but about the
    # mechanism, and the reader wants to know whether to touch it.
    "Course website (auto-deployed on push)": (
        "[do not touch]: Course website (auto-deployed)"
    ),
    # The wording before that one. Found on a semester scaffolded early enough to predate the
    # rename, which is the whole reason this table is a mapping and not a single pair: a
    # description set at creation stays until something converges it, so every wording we
    # have ever written needs a row here or that org keeps it forever.
    "Cohort course website (auto-deployed on push)": (
        "[do not touch]: Course website (auto-deployed)"
    ),
}

# Per TIER, because one old wording wants two different new ones. A semester org's `.github`
# is machine-owned scaffolding faculty never open; a COURSE org's is where they actually
# work - it holds dsl-course.yml and every workflow they run. A flat old -> new mapping
# cannot tell those apart, so the tier picks the table. Same forcing function as above: a
# reworded literal must be added here or convergence silently stops.
# The semester config repo's description, as bootstrap_course creates it.
_CONFIG_REPO_DESCRIPTION = (
    "[visible to instructors only]: Everything you configure for this semester is here - "
    "student roster, teams, schedule, and marking. Students never see it, and no PII "
    "leaves this repo."
)
SUPERSEDED_SEMESTER_DESCRIPTIONS = {
    "Org profile and configuration": "[do not touch]: Org profile and configuration",
    "PRIVATE cohort config - roster (students.csv). No PII leaves here.": (
        _CONFIG_REPO_DESCRIPTION
    ),
    # The wording before "cohort" and "term" became "semester" (decision 0012).
    "[visible to instructors only]: Everything you configure for this cohort is here - "
    "student roster, teams, term schedule, and marking. Students never see it, and no "
    "PII leaves this repo.": _CONFIG_REPO_DESCRIPTION,
}
# Descriptions that carry the repo's own name, so no one literal can key them: the old
# ENDING -> the new one. `assign` names each semester-side template `<slug> - ...`.
SUPERSEDED_DESCRIPTION_ENDINGS = {
    " - cohort assignment template": " - semester assignment template",
}
SUPERSEDED_COURSE_DESCRIPTIONS = {
    "Org profile and configuration": "[control panel]: Org profile & configuration",
}


class Converged(NamedTuple):
    """What one convergence pass did: how many repos it CHANGED, and how many changes it
    could not make. One shape for all three passes, so the orchestrator decides which
    failures red a run rather than each pass deciding by what it happens to return."""

    changed: int = 0
    failures: int = 0


def current_description(said: str, tier: str | None = None) -> str | None:
    """The wording a repo described as `said` should carry now, or None when it already
    does (or is not one we wrote). `tier` as in `converge_descriptions`."""
    said = said.strip()
    superseded = SUPERSEDED_DESCRIPTIONS | (
        SUPERSEDED_COURSE_DESCRIPTIONS
        if tier == "course"
        else SUPERSEDED_SEMESTER_DESCRIPTIONS
    )
    return superseded.get(said) or next(
        (
            said[: -len(old)] + new
            for old, new in SUPERSEDED_DESCRIPTION_ENDINGS.items()
            if said.endswith(old)
        ),
        None,
    )


def converge_descriptions(
    org: str, repos: list[dict], tier: str | None = None
) -> Converged:
    """Update every repo in `repos` whose description we have since reworded.

    `tier` (`discovery.org_tier`) selects the tier-specific table on top of the shared
    one: the same old `.github` wording becomes "[do not touch]" on a semester org and
    "[control panel]" on a course org, because they are opposite instructions to the same
    reader. None - a listing that cannot place the org - reads as a semester, the same way
    the faculty floor does.

    A GitHub description is only ever set at repo CREATION, so a wording fix otherwise
    never reaches a repo that already exists - while being the "What it's for" column on
    the org's landing page. This is the convergence path for it.

    Costs no reads: `repos` is the listing the caller already holds (list_org_repos asks
    for `description` in the same paginated call), so the only requests made are a PATCH
    per genuinely-drifted repo. The dicts are updated in place as well, so a caller that
    renders the listing straight afterwards shows the new wording in the same run rather
    than one run late.

    A failed PATCH is a line, not an exception; whether it reds the run is the caller's
    call (see seed._converge_org_metadata).
    """
    changed = 0
    failures = 0
    for repo in repos:
        if repo.get("archived"):
            continue  # GitHub refuses the PATCH; a frozen semester logged one failure a night
        want = current_description(repo.get("description") or "", tier)
        if not want:
            continue
        code, _ = gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{org}/{repo['name']}",
            "--field",
            f"description={want}",
        )
        if code == 0:
            repo["description"] = want
            log_ok(f"{repo['name']} description -> current wording")
            changed += 1
        else:
            log(f"  ({repo['name']}: could not update the description)")
            failures += 1
    return Converged(changed, failures)


def create_repo(
    org: str,
    name: str,
    private: bool = True,
    description: str = "",
    is_template: bool = False,
    *,
    person: bool = False,
) -> bool:
    """Create a repo. Idempotent - treats existing repo as success.

    `person` marks a repo NAMED AFTER A STUDENT (`grades-<handle>`). These workflows run in
    the org's PUBLIC `.github`, so its happy-path lines go through `log_person`. The failure
    line still names the repo: an error faculty must act on is worse unactionable.

    Sets `description` only on creation. Bringing an EXISTING repo's description up to a
    reworded one is converge_descriptions' job, off the listing the refresh already
    holds - not this function's, which would have to pay a read per call to find out.

    Refuses a RETIRED name (`course.RETIRED_REPO_NAMES`): a repo created there would end
    the redirect GitHub keeps from the renamed one, and with it every link already sent."""
    if name in RETIRED_REPO_NAMES:
        log_err(
            f"refused to create {org}/{name}: that name was retired and redirects to "
            f"its renamed repo - {NOT_MIGRATED}: run the migration"
        )
        return False
    args = [
        "api",
        "--method",
        "POST",
        f"orgs/{org}/repos",
        "--field",
        f"name={name}",
        "--field",
        f"private={str(private).lower()}",
        "--field",
        f"is_template={str(is_template).lower()}",
    ]
    if description:
        args += ["--field", f"description={description}"]
    code, out = gh(*args)
    if code == 0:
        (log_person if person else log_ok)(f"repo created: {org}/{name}")
        return True
    if is_already_exists(out):
        (log_person if person else log_skip)(f"repo {org}/{name}")
        return True
    log_err(f"failed to create repo {org}/{name}: {out[:200]}")
    return False


# Path components a PUBLISHED course page must never carry - matched by NAME, at every
# depth, case-insensitively, and as glob patterns so `.env.local` is caught alongside
# `.env`. The public site copies whole discovered session folders wholesale, so anything a
# faculty member happens to keep beside their teaching material is published with it: a
# `solution/` next to the lab it answers, the `grading_config.yml` that says how it is
# marked, the
# hidden `tests/`, a `.env` with a live key. None of those is a release decision anyone
# made; they are what "copy the folder" means.
#
# NOT a release policy for the semester path - `deploy` deliberately releases what faculty
# name, including a solution, because a semester repo is private and marking sometimes needs
# one. This is the PUBLIC site, where there is no such case.
PUBLICATION_DENYLIST = (
    "solution",
    "solutions",
    "grading_config.yml",
    "grading.yml",  # the pre-rename name; the engine stopped reading it, this list has not
    "tests",
    ".env",
    ".env.*",
    ".git",
)


def is_denied_publication(name: str) -> bool:
    """Whether one path COMPONENT is on PUBLICATION_DENYLIST."""
    lowered = name.casefold()
    return any(fnmatch(lowered, pattern) for pattern in PUBLICATION_DENYLIST)


def has_denied_component(path: str) -> bool:
    """Whether any component of `path` is on PUBLICATION_DENYLIST."""
    return any(is_denied_publication(part) for part in path.split("/") if part)


# Artefacts that are never course material, in any course, on any page - matched by NAME,
# at every depth, case-insensitively. What a file manager drops in a folder it opened
# (`.DS_Store`, `Thumbs.db`, `desktop.ini`), the empty placeholder that holds an empty
# folder open in git (`.gitkeep`), and the caches an interpreter and a notebook leave
# behind (`__pycache__/`, `.ipynb_checkpoints/`). A semester's public site listed four of
# these as its course materials and counted them into its sessions - "6 files" over a
# session holding five, one of which was a `.gitkeep`.
#
# Kept apart from PUBLICATION_DENYLIST deliberately, and it is not an oversight that
# neither list mentions the other. That one is a SECURITY boundary: a `solution/`, a
# `grading_config.yml`, a `.env` must not leak, and every entry earns its place by what it
# would
# cost to publish. Nothing here leaks anything - it is clutter. Folding the two together
# would let an edit meant for a list of nuisances widen the list that keeps answers away
# from a class.
#
# NOT a rule about dots, which cannot be written honestly: `.Rprofile`, `.env.example` and
# a `.devcontainer/` are real course material a dot rule would hide, while `__pycache__/`
# and `node_modules/` are clutter and are not dotted. A closed list of NAMES is the only
# form of this rule that is true, which is also why it is short and stays short.
#
# The rule is "never copied out to students, never listed on a page" - NOT "never
# committed". `.gitkeep` still does its job wherever this toolkit writes one (the scaffold's
# empty `lectures/01_session-1/`, the site repo's own collections) and in a course's SOURCE
# repo, where it is what holds an unwritten future session open until someone writes it.
NEVER_MATERIAL = frozenset(
    {
        ".DS_Store",
        ".gitkeep",
        "Thumbs.db",
        "desktop.ini",
        "__pycache__",
        ".ipynb_checkpoints",
    }
)

_NEVER_MATERIAL_FOLDED = frozenset(n.casefold() for n in NEVER_MATERIAL)


def is_never_material(name: str) -> bool:
    """Whether one path COMPONENT is on NEVER_MATERIAL.

    Case-insensitive: `.DS_Store`, `.ds_store`, `Thumbs.db` and `desktop.ini` all appear in
    the wild, and which spelling a machine wrote is not a decision about course content."""
    return name.casefold() in _NEVER_MATERIAL_FOLDED


def has_never_material_component(path: str) -> bool:
    """Whether any component of `path` is on NEVER_MATERIAL.

    Component-wise, because two entries are DIRECTORY names: a copytree filter skipping
    `__pycache__` skips the subtree with it, but a listing path holds `/`-joined strings,
    where `__pycache__/lab.cpython-312.pyc` is one entry and only its parent gives it
    away."""
    return any(is_never_material(part) for part in path.split("/") if part)


def _failed_on(person: bool, public: str, detail: str) -> None:
    """One failure line, public or split, depending on whether the repo names somebody.

    The twin of `gh_contents._failed`; both exist because the failure branches are exactly
    where the public-log rule was forgotten, and a name reaches the log the moment one of
    them is written without thinking about it."""
    if person:
        log_err_person(public, detail)
    else:
        log_err(detail)


def topic_name(text: str) -> str:
    """`text` as GitHub stores a topic: lowercase kebab. Shared by the write and by every
    comparison against a live topic list, which must agree or a repo converges nightly."""
    return text.lower().replace("_", "-")


def set_repo_topics(
    org: str, repo: str, topics: list[str], *, person: bool = False
) -> bool:
    """Replace the full topic list on a repo (GitHub limit: 20 topics, lowercase kebab).

    `person=True` says the repo is somebody's - a gradebook, a submission repo. Every
    caller that tags one already knew not to name it and said so in its own failure line;
    the line HERE named it anyway, which is the half that reached the public log."""
    normalised = sorted({topic_name(t) for t in topics if t})
    args = [
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{repo}/topics",
        "-H",
        "Accept: application/vnd.github+json",
    ]
    for t in normalised:
        args += ["--field", f"names[]={t}"]
    code, out = gh_settled(*args)
    if code == 0:
        return True
    detail = f"failed to set topics on {org}/{repo}: {out[:200]}"
    if person:
        log_err_person(
            f"could not tag a repo in {org} - the nightly sweep converges it", detail
        )
    else:
        log_err(detail)
    return False


def ensure_label(
    org: str,
    repo: str,
    name: str,
    *,
    color: str,
    description: str,
    person: bool = False,
) -> bool:
    """Create an issue label if the repo doesn't have it. Already-there is success.

    Deliberately create-only: an existing label's colour/description is left alone, and a
    rename would orphan every issue already carrying the old name. The caller cares that
    the NAME exists - GitHub silently drops a label an issue form declares when the repo
    doesn't have it, so a form's `labels:` line is a request, not a guarantee."""
    code, out = gh(
        "api",
        # Spelt out so `ghcli._is_mutating` sees a write and the pacer counts it: a POST
        # inferred from the presence of `--field` is a POST the governor cannot see.
        "--method",
        "POST",
        f"repos/{org}/{repo}/labels",
        "--field",
        f"name={name}",
        "--field",
        f"color={color}",
        "--field",
        f"description={description}",
    )
    if code == 0 or is_already_exists(out):
        return True
    if person:
        log_err_person(
            f"could not create the '{name}' label on a repo in {org}",
            f"could not create label '{name}' on {org}/{repo}: {out[:160]}",
        )
    else:
        log_err(f"could not create label '{name}' on {org}/{repo}: {out[:160]}")
    return False


def add_collaborator(
    org: str,
    repo: str,
    login: str,
    permission: str = "push",
    person: bool = False,
) -> bool:
    """Add a collaborator to a repo. permission: pull | triage | push | maintain | admin.

    `person=True` when the repo is somebody's - the failure line then names them only in
    the verbose log (see `log.log_err_person`)."""
    code, out = gh_settled(
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{repo}/collaborators/{login}",
        "--field",
        f"permission={permission}",
    )
    if code == 0:
        return True
    if person:
        log_err_person(
            f"failed to grant one collaborator access in {org}",
            f"failed to add {login} to {org}/{repo}: {out[:200]}",
        )
    else:
        log_err(f"failed to add {login} to {org}/{repo}: {out[:200]}")
    return False


def _direct_logins(
    org: str, repo: str, endpoint: str, jq: str
) -> tuple[list[str] | None, str]:
    """The non-blank lines `jq` selected from one paginated listing of `org/repo`, or
    `(None, the error text)` when the call itself failed.

    The three readers below ask GitHub in exactly the same way and differ only in WHICH
    listing they read and what a failure MEANS to them - a 404 is "no such repo, so there
    is nothing to revoke on it" to two of them and a read it may not guess at to the
    third. That answer stays with each caller; the call does not."""
    code, out = gh("api", "--paginate", f"repos/{org}/{repo}/{endpoint}", "--jq", jq)
    if code != 0:
        return None, out
    return [line.strip() for line in out.splitlines() if line.strip()], out


def is_collaborator(
    org: str, repo: str, login: str, *, person: bool = False
) -> bool | None:
    """Whether `login` holds a DIRECT collaborator grant on `org/repo`.

    Read from the `affiliation=direct` LISTING, not from
    `GET /collaborators/{login}` - that endpoint 204s for anyone who can reach the repo
    at all, including through a team and by being an org owner. Its answer is therefore
    "has access", and the one caller here revokes on it: every faculty member and the bot
    would have read as a direct collaborator on every repo named after a handle they
    happen to share, and the DELETE that followed reported a revoke that removed nothing.

    None means the answer could not be read. Kept distinct from False on purpose: the
    caller is about to REVOKE access, and a rate limit or a network drop must never read
    as "not a collaborator, nothing to do" - nor, worse, be acted on either way."""
    logins, out = _direct_logins(
        org, repo, "collaborators?affiliation=direct&per_page=100", ".[].login"
    )
    if logins is not None:
        return login.casefold() in {ln.casefold() for ln in logins}
    if is_missing_resource(out):
        return False  # no such repo - nothing to revoke on it
    _failed_on(
        person,
        f"could not read a repo's collaborators in {org}",
        f"could not check whether {login} collaborates on {org}/{repo}: {out[:160]}",
    )
    return None


def direct_collaborators(
    org: str, repo: str, *, person: bool = False
) -> frozenset[str] | None:
    """Every login that already holds a DIRECT grant on `repo`, casefolded - the
    collaborators plus the people whose invitation is still un-accepted. None when the
    answer could not be read.

    DIRECT, and the name says so: a team grant and an org owner reach the repo without
    appearing here, which is what makes this set safe to converge AGAINST - the caller
    revokes off it, and faculty and the bot must never be in what it revokes.

    TWO listings for a whole semester, where asking `is_collaborator` per student is two
    calls per student. That is what makes the shared drop box's grant loop affordable: the
    handout re-fires on every tick (which is how a late onboarder gets their access), and
    one repo serving fifty units has no per-unit repo whose existence records the grant.

    The invitations half is not an optimisation. A student granted before they accepted
    their org invite is an INVITATION and not a collaborator row, so a set built from the
    collaborators alone would re-PUT for every un-onboarded student on every tick, for as
    long as they never accept.

    None, not the empty set, when either listing fails: the caller's answer to "we could
    not look" is to grant everyone again - the PUT is idempotent, and an over-granted one
    costs a call where an under-granted one costs a student their submission."""
    logins: set[str] = set()
    for endpoint, jq in (
        ("collaborators?affiliation=direct&per_page=100", ".[].login"),
        ("invitations?per_page=100", '.[].invitee.login // ""'),
    ):
        found, out = _direct_logins(org, repo, endpoint, jq)
        if found is None:
            _failed_on(
                person,
                f"could not read who already has access to a repo in {org}",
                f"could not read {org}/{repo}'s {endpoint.split('?')[0]}: {out[:160]}",
            )
            return None
        logins |= {login.casefold() for login in found}
    return frozenset(logins)


def remove_collaborator(
    org: str, repo: str, login: str, *, person: bool = False
) -> bool:
    """Revoke a direct collaborator grant. Idempotent - GitHub 204s either way."""
    code, out = gh_settled(
        "api", "--method", "DELETE", f"repos/{org}/{repo}/collaborators/{login}"
    )
    if code == 0:
        return True
    _failed_on(
        person,
        f"could not revoke a collaborator in {org}",
        f"could not remove {login} from {org}/{repo}: {out[:160]}",
    )
    return False


def pending_invitations(
    org: str, repo: str, login: str, *, person: bool = False
) -> list[str] | None:
    """The ids of `login`'s un-accepted invitations to `org/repo`, `[]` if there are none,
    None if the listing could not be read.

    A collaborator granted before the student accepted their org invite is an INVITATION,
    not a collaborator row - so `is_collaborator` says no and `remove_collaborator` removes
    nothing, while the invitation stays live and accepting it hands back `maintain`.

    None is kept distinct from `[]` for the same reason as `is_collaborator`: the caller is
    about to revoke, and an unreadable listing must never read as "nothing to cancel"."""
    rows, out = _direct_logins(
        org, repo, "invitations?per_page=100", ".[] | [.id, .invitee.login] | @tsv"
    )
    if rows is None:
        if is_missing_resource(out):
            return []  # no such repo - nothing to cancel on it
        _failed_on(
            person,
            f"could not read a repo's invitations in {org}",
            f"could not list invitations on {org}/{repo}: {out[:160]}",
        )
        return None
    fold = login.casefold()
    ids = []
    for row in rows:
        invitation_id, _, invitee = row.partition("\t")
        if invitee.strip().casefold() == fold:
            ids.append(invitation_id.strip())
    return ids


def cancel_invitation(
    org: str, repo: str, invitation_id: str, *, person: bool = False
) -> bool:
    """Cancel one repo invitation by id."""
    code, out = gh(
        "api", "--method", "DELETE", f"repos/{org}/{repo}/invitations/{invitation_id}"
    )
    if code == 0:
        return True
    _failed_on(
        person,
        f"could not cancel an invitation in {org}",
        f"could not cancel invitation {invitation_id} on {org}/{repo}: {out[:160]}",
    )
    return False


def generate_from_template(
    template_org: str,
    template_name: str,
    owner: str,
    name: str,
    private: bool = True,
    description: str = "",
    *,
    person: bool = False,
) -> bool:
    """Create a repo from a template. Idempotent.

    `person` gates the already-exists line, the only one this logs; the success narration
    belongs to the caller. A `<slug>-<handle>` repo must not be named in a public log."""
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{template_org}/{template_name}/generate",
        "-H",
        "Accept: application/vnd.github+json",
        "--field",
        f"owner={owner}",
        "--field",
        f"name={name}",
        "--field",
        f"private={str(private).lower()}",
        "--field",
        f"description={description}",
    )
    if code == 0:
        return True
    if is_already_exists(out):
        (log_person if person else log_skip)(f"repo {owner}/{name}")
        return True
    log_err(f"failed to generate {owner}/{name} from template: {out[:200]}")
    return False
