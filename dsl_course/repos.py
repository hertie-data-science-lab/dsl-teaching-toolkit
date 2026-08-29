"""Repositories themselves: does one exist, what is its default branch, create it,
topic and describe it, add a collaborator - and what a published page may never carry.
"""

from __future__ import annotations

from fnmatch import fnmatch
from functools import cache
from typing import NamedTuple

from .ghcli import gh, is_already_exists, is_missing_resource
from .log import log, log_err, log_ok, log_skip


def repo_missing(org: str, name: str) -> bool:
    """Whether GitHub positively says the repo is NOT there (a 404). The shape for a
    caller about to record something permanent on the strength of absence: a 5xx or a
    rate limit is neither present nor absent, and must read as "could not tell"."""
    code, out = gh("api", f"repos/{org}/{name}")
    return code != 0 and is_missing_resource(out)


def repo_exists(org: str, name: str) -> bool:
    """Whether the repo is there. OPTIMISTIC: any read failure reads as absent, because
    this answers a create-if-missing question where guessing wrong costs a retry.

    Its neighbour `org_exists` is deliberately the opposite shape - it raises rather than
    call an unreadable org deleted - because its callers act destructively on a False.
    Reach for that one whenever absence is going to remove something."""
    code, _ = gh("api", f"repos/{org}/{name}")
    return code == 0


def org_exists(org: str) -> bool:
    """Whether `org` is still a live GitHub org.

    The liveness half of discovery, and the ONLY evidence an org is gone that anything
    here may act on. The topic search behind the inventory is eventually consistent, and
    generously so: a deleted org kept coming back from `gh search repos topic:dsl-cohort`
    for ten days after the org itself was gone. The search says what is INDEXED; this says
    what is THERE.

    Fails CLOSED, which is the whole point - only an unambiguous 404 is absence. A 403, a
    5xx, a rate limit or a timeout all mean "could not tell", and both callers act
    destructively on a False (a row dropped from a generated page, a cohort unregistered
    from every nightly sync). Reading "could not tell" as "deleted" would do that on any
    transient failure, so it raises instead. `repo_exists` above is deliberately the
    opposite shape: it answers a cheap should-I-create question where a wrong guess costs
    a retry, not a deletion.

    Even the 404 is weaker evidence than it looks: GitHub answers 404, not 403, for an org
    the TOKEN cannot see, so a bot removed from one org - or running on a rotated token
    that was never re-invited - reads identically to a deleted org. False therefore means
    "not visible to this token", and a caller that acts destructively on it needs more
    than one look (see seed._live_cohorts, which requires two consecutive misses)."""
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
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".private")
    return out.strip() != "false" if code == 0 else True


def repo_is_archived(org: str, name: str) -> bool:
    """Return True if the repo is archived (assume LIVE if the check fails).

    Archived repos are read-only - every write 403s. The optimistic default is deliberate:
    a transient API failure must not silently skip a live cohort's refresh. Guess wrong
    that way and the write itself fails loudly, which is the outcome we want.
    """
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".archived")
    return out.strip() == "true" if code == 0 else False


def get_default_branch(org: str, name: str) -> str:
    """Return the default branch of a repo. Falls back to 'main'."""
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".default_branch")
    if code == 0 and out:
        return out
    return "main"


@cache
def default_branch(org: str, name: str) -> str:
    """The default branch, RAISING if it cannot be read - and cached per repo.

    The fail-loud twin of get_default_branch, for writers. Guessing "main" is the right
    default for a reader that would otherwise just find nothing; for a write it aims a
    commit at a branch that may not be the one that exists, so put_files would rather fail
    than land work somewhere nobody is looking.

    Cached because a repo's default branch cannot change mid-run, and one run commits to
    the same repo more than once (a cohort's classroom-config takes both a contract and a
    samples commit; an org's `.github` takes both a workflows and a READMEs commit).
    functools.cache does not memoise a raised exception, so a transient failure is retried
    rather than pinned for the life of the process."""
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".default_branch")
    if code != 0 or not out.strip():
        raise RuntimeError(f"could not read {org}/{name}'s default branch: {out[:200]}")
    return out.strip()


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
    # The wording before that one. Found on a cohort scaffolded early enough to predate the
    # rename, which is the whole reason this table is a mapping and not a single pair: a
    # description set at creation stays until something converges it, so every wording we
    # have ever written needs a row here or that org keeps it forever.
    "Cohort course website (auto-deployed on push)": (
        "[do not touch]: Course website (auto-deployed)"
    ),
}

# Per TIER, because one old wording wants two different new ones. A cohort org's `.github`
# is machine-owned scaffolding faculty never open; a COURSE org's is where they actually
# work - it holds dsl-course.yml and every workflow they run. A flat old -> new mapping
# cannot tell those apart, so the tier picks the table. Same forcing function as above: a
# reworded literal must be added here or convergence silently stops.
SUPERSEDED_COHORT_DESCRIPTIONS = {
    "Org profile and configuration": "[do not touch]: Org profile and configuration",
    # Every org still carries the wording on the LEFT, so this is a single hop rather than
    # a chain: the interim text this replaced never reached one.
    "PRIVATE cohort config - roster (students.csv). No PII leaves here.": (
        "[visible to instructors only]: Everything you configure for this cohort is "
        "here - student roster, teams, term schedule, and marking. Students never see "
        "it, and no PII leaves this repo."
    ),
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


def converge_descriptions(
    org: str, repos: list[dict], tier: str | None = None
) -> Converged:
    """Update every repo in `repos` whose description we have since reworded.

    `tier` (`discovery.org_tier`) selects the tier-specific table on top of the shared
    one: the same old `.github` wording becomes "[do not touch]" on a cohort org and
    "[control panel]" on a course org, because they are opposite instructions to the same
    reader. None - a listing that cannot place the org - reads as a cohort, the same way
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
    superseded = SUPERSEDED_DESCRIPTIONS | (
        SUPERSEDED_COURSE_DESCRIPTIONS
        if tier == "course"
        else SUPERSEDED_COHORT_DESCRIPTIONS
    )
    changed = 0
    failures = 0
    for repo in repos:
        if repo.get("archived"):
            continue  # GitHub refuses the PATCH; a frozen cohort logged one failure a night
        want = superseded.get((repo.get("description") or "").strip())
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
) -> bool:
    """Create a repo. Idempotent - treats existing repo as success.

    Sets `description` only on creation. Bringing an EXISTING repo's description up to a
    reworded one is converge_descriptions' job, off the listing the refresh already
    holds - not this function's, which would have to pay a read per call to find out."""
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
        log_ok(f"repo created: {org}/{name}")
        return True
    if is_already_exists(out):
        log_skip(f"repo {org}/{name}")
        return True
    log_err(f"failed to create repo {org}/{name}: {out[:200]}")
    return False


# Path components a PUBLISHED course page must never carry - matched by NAME, at every
# depth, case-insensitively, and as glob patterns so `.env.local` is caught alongside
# `.env`. The public site copies whole discovered session folders wholesale, so anything a
# faculty member happens to keep beside their teaching material is published with it: a
# `solution/` next to the lab it answers, the `grading.yml` that says how it is marked, the
# hidden `tests/`, a `.env` with a live key. None of those is a release decision anyone
# made; they are what "copy the folder" means.
#
# NOT a release policy for the cohort path - `deploy` deliberately releases what faculty
# name, including a solution, because a cohort repo is private and marking sometimes needs
# one. This is the PUBLIC site, where there is no such case.
PUBLICATION_DENYLIST = (
    "solution",
    "solutions",
    "grading.yml",
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


def set_repo_topics(org: str, repo: str, topics: list[str]) -> bool:
    """Replace the full topic list on a repo (GitHub limit: 20 topics, lowercase kebab)."""
    normalised = sorted({t.lower().replace("_", "-") for t in topics if t})
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
    code, out = gh(*args)
    if code == 0:
        return True
    log_err(f"failed to set topics on {org}/{repo}: {out[:200]}")
    return False


def add_collaborator(org: str, repo: str, login: str, permission: str = "push") -> bool:
    """Add a collaborator to a repo. permission: pull | triage | push | maintain | admin."""
    code, out = gh(
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{repo}/collaborators/{login}",
        "--field",
        f"permission={permission}",
    )
    if code == 0:
        return True
    log_err(f"failed to add {login} to {org}/{repo}: {out[:200]}")
    return False


def is_collaborator(org: str, repo: str, login: str) -> bool | None:
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
    code, out = gh(
        "api",
        "--paginate",
        f"repos/{org}/{repo}/collaborators?affiliation=direct&per_page=100",
        "--jq",
        ".[].login",
    )
    if code == 0:
        return login.casefold() in {ln.strip().casefold() for ln in out.splitlines()}
    if is_missing_resource(out):
        return False  # no such repo - nothing to revoke on it
    log_err(
        f"could not check whether {login} collaborates on {org}/{repo}: {out[:160]}"
    )
    return None


def remove_collaborator(org: str, repo: str, login: str) -> bool:
    """Revoke a direct collaborator grant. Idempotent - GitHub 204s either way."""
    code, out = gh(
        "api", "--method", "DELETE", f"repos/{org}/{repo}/collaborators/{login}"
    )
    if code == 0:
        return True
    log_err(f"could not remove {login} from {org}/{repo}: {out[:160]}")
    return False


def generate_from_template(
    template_org: str,
    template_name: str,
    owner: str,
    name: str,
    private: bool = True,
    description: str = "",
) -> bool:
    """Create a repo from a template. Idempotent."""
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
        log_skip(f"repo {owner}/{name}")
        return True
    log_err(f"failed to generate {owner}/{name} from template: {out[:200]}")
    return False
