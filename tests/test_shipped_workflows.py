"""The workflow FILES this repo ships: its own `.github/workflows/`, plus every template
seeded verbatim into a course/cohort org.

Same operational properties the renderers are held to in test_renderers.py, enforced on
the files rather than on the functions - so a hand-written workflow can't quietly take the
default token scopes, run unbounded, interpolate an expression into a shell, or float an
action on a movable tag. Ownership is deliberate: anything asserted about a shipped .yml
lives here, and the per-template behaviour tests (test_welcome_templates.py) assert only
what is unique to that template.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from test_renderers import ALL_RENDERED

ROOT = Path(__file__).resolve().parents[1]


def _shipped_workflows() -> dict[str, dict]:
    out = {}
    for path in [
        *(ROOT / ".github" / "workflows").glob("*.yml"),
        *(ROOT / "templates").rglob("*.yml"),
    ]:
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and "jobs" in doc:
            out[path.relative_to(ROOT).as_posix()] = doc
    return out


SHIPPED_WORKFLOWS = _shipped_workflows()


def test_the_shipped_workflow_sweep_sees_them_all():
    # ci, bootstrap-org, promote, refresh-inventory, token-canary, both dispatchers,
    # validate-schedule, onboard, team-formation - a broken glob would make the tests
    # below vacuous.
    assert len(SHIPPED_WORKFLOWS) >= 10


@pytest.mark.parametrize("rel", sorted(SHIPPED_WORKFLOWS))
def test_shipped_workflows_declare_permissions_and_bound_their_jobs(rel):
    doc = SHIPPED_WORKFLOWS[rel]
    assert "permissions" in doc, f"{rel} takes the default token scopes"
    for name, job in doc["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int), f"{rel}:{name}"


@pytest.mark.parametrize("rel", sorted(SHIPPED_WORKFLOWS))
def test_shipped_workflows_route_values_through_env(rel):
    for name, job in SHIPPED_WORKFLOWS[rel]["jobs"].items():
        for step in job.get("steps", []):
            assert "${{" not in step.get("run", ""), f"{rel}:{name}"


@pytest.mark.parametrize("rel", sorted(SHIPPED_WORKFLOWS))
def test_shipped_workflows_pin_actions_to_commit_shas(rel):
    for job in SHIPPED_WORKFLOWS[rel]["jobs"].values():
        for step in job.get("steps", []):
            if "uses" not in step:
                continue
            assert re.fullmatch(r"[0-9a-f]{40}", step["uses"].partition("@")[2]), (
                f"{rel}: {step['uses']}"
            )


def _action_shas() -> dict[str, dict[str, set[str]]]:
    """`actions/checkout` -> {sha -> the places pinning it}, across the whole estate."""
    seen: dict[str, dict[str, set[str]]] = {}
    sources = {f"rendered:{n}": r for n, r in ALL_RENDERED.items()}
    for rel, doc in SHIPPED_WORKFLOWS.items():
        sources[rel] = yaml.safe_dump(doc, width=10**6)
    for where, text in sources.items():
        for ref in re.findall(r"uses: (\S+)@([0-9a-f]{40})", text):
            action, sha = ref
            seen.setdefault(action, {}).setdefault(sha, set()).add(where)
    return seen


def test_each_action_is_pinned_to_exactly_one_sha_estate_wide():
    # Pinning is only half the job: two pins of the same action at different shas means one
    # of them was bumped and the other forgotten, so a security bump reaches some workflows
    # and not others - and nothing else would ever notice. The renderers all read the same
    # _CHECKOUT / _SETUP_PYTHON constants, so the drift this catches is a hand-written
    # shipped file (or a template) that pinned its own copy.
    for action, by_sha in sorted(_action_shas().items()):
        assert len(by_sha) == 1, (
            f"{action} is pinned to {len(by_sha)} different shas: "
            + "; ".join(
                f"{sha[:12]} in {sorted(where)}" for sha, where in by_sha.items()
            )
        )


def test_the_sha_agreement_sweep_actually_sees_the_estate():
    # A regex that stopped matching would make the test above pass on an empty dict.
    actions = _action_shas()
    assert {"actions/checkout", "actions/setup-python"} <= set(actions)


def _promote_job() -> dict:
    return SHIPPED_WORKFLOWS[".github/workflows/promote.yml"]["jobs"]["promote"]


def test_promote_pushes_the_tiers_with_a_deploy_key_not_the_bot():
    # The tier branches carry a ruleset whose only bypass actor is "deploy keys", which no
    # account and no Actions token can be - so a bot token in this job is both the account
    # push that ruleset exists to refuse and a far wider credential than a push needs.
    job = _promote_job()
    assert "DSL_BOT_TOKEN" not in yaml.safe_dump(job)
    step = next(s for s in job["steps"] if s.get("name", "").startswith("Fast-forward"))
    assert step["env"]["PROMOTE_DEPLOY_KEY"] == "${{ secrets.PROMOTE_DEPLOY_KEY }}"
    assert 'git remote set-url origin "git@github.com:' in step["run"]
    # ssh-keyscan trusts whatever answers, so it would have written a substituted
    # github.com's key into known_hosts and pushed the deploy key straight at it.
    assert "ssh-keyscan" not in step["run"]
    assert "gh api meta --jq '.ssh_keys[]'" in step["run"]


def _promote_refresh_job() -> dict:
    return SHIPPED_WORKFLOWS[".github/workflows/promote.yml"]["jobs"]["refresh-orgs"]


def _refresh_step() -> dict:
    return next(
        s
        for s in _promote_refresh_job()["steps"]
        if s.get("name") == "Refresh every org on this tier"
    )


def test_promote_refreshes_orgs_from_the_promoted_checkout():
    # The fan-out must run the refresh IN PROCESS, from the code that was just promoted.
    # Dispatching each org's own "Refresh actions" instead runs the toolkit at the ref
    # already baked into that org's workflow file, so an org whose central_ref has just
    # changed tier is re-rendered by the OLD tier's code and never converges.
    job = _promote_refresh_job()
    checkout = next(s for s in job["steps"] if "checkout" in s.get("uses", ""))
    assert checkout["with"]["ref"] == "${{ needs.promote.outputs.sha }}"
    run = _refresh_step()["run"]
    assert "python3 -m dsl_course.seed refresh --course-org" in run
    assert "gh workflow run refresh-actions.yml" not in run


def test_promote_refresh_carries_both_bot_tokens():
    # seed refresh reads GH_TOKEN for the API and DSL_BOT_TOKEN to propagate the repo
    # secret (ghcli.bot_token refuses to publish a token that is only GH_TOKEN).
    env = _refresh_step()["env"]
    assert env["GH_TOKEN"] == "${{ secrets.DSL_BOT_TOKEN }}"
    assert env["DSL_BOT_TOKEN"] == "${{ secrets.DSL_BOT_TOKEN }}"


def test_bootstrap_org_offers_no_dev_tier():
    # `main` is the dev tier - nobody live. An org bootstrapped onto it runs every merge
    # as production the moment it lands, with no promotion in between; a soak goes on
    # staging, which is what that tier is for.
    doc = SHIPPED_WORKFLOWS[".github/workflows/bootstrap-org.yml"]
    trigger = doc.get("on", doc.get(True))
    options = trigger["workflow_dispatch"]["inputs"]["central_ref"]["options"]
    assert options == ["release", "staging"]


def _site_build_step() -> dict:
    doc = SHIPPED_WORKFLOWS["templates/site/.github/workflows/deploy.yml"]
    return next(
        s for s in doc["jobs"]["build"]["steps"] if s.get("name") == "Build site"
    )


def test_the_site_build_tells_github_metadata_which_repo_it_is_building():
    # jekyll-github-metadata synthesises `site.title` and `site.description` from the
    # repository, and dies with "No repo name found" unless something names it - the
    # Actions checkout's origin is not a name it will take. Every cohort site went down
    # this way under theme v2.0.0, whose layouts read `site.title` as their fallback.
    assert _site_build_step()["env"]["PAGES_REPO_NWO"] == "${{ github.repository }}"
