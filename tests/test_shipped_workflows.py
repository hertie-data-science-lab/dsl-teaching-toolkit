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

import json
import os
import re
import shutil
import subprocess
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
    # ci, bootstrap-org, promote, deploy-main, refresh-tier, refresh-inventory,
    # token-canary, both dispatchers, validate-schedule, onboard, team-formation - a broken
    # glob would make the tests below vacuous.
    assert len(SHIPPED_WORKFLOWS) >= 12


@pytest.mark.parametrize("rel", sorted(SHIPPED_WORKFLOWS))
def test_shipped_workflows_declare_permissions_and_bound_their_jobs(rel):
    doc = SHIPPED_WORKFLOWS[rel]
    assert "permissions" in doc, f"{rel} takes the default token scopes"
    for name, job in doc["jobs"].items():
        if "uses" in job:
            # GitHub refuses `timeout-minutes` on a job that calls a reusable workflow.
            # What it runs is that workflow's own jobs, which are in this sweep too, so
            # the bound is asserted there rather than lost.
            assert "permissions" in job, f"{rel}:{name}"
            continue
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


def _fast_forward_step() -> dict:
    return next(
        s
        for s in _promote_job()["steps"]
        if s.get("name", "").startswith("Fast-forward")
    )


def test_promote_pushes_the_tier_with_a_deploy_key_not_the_bot():
    # `release` carries a ruleset whose only bypass actor is "deploy keys", which no
    # account and no Actions token can be - so a bot token in this job is both the account
    # push that ruleset exists to refuse and a far wider credential than a push needs.
    assert "DSL_BOT_TOKEN" not in yaml.safe_dump(_promote_job())
    step = _fast_forward_step()
    assert step["env"]["PROMOTE_DEPLOY_KEY"] == "${{ secrets.PROMOTE_DEPLOY_KEY }}"
    assert 'git remote set-url origin "git@github.com:' in step["run"]
    # ssh-keyscan trusts whatever answers, so it would have written a substituted
    # github.com's key into known_hosts and pushed the deploy key straight at it.
    assert "ssh-keyscan" not in step["run"]
    assert "gh api meta --jq '.ssh_keys[]'" in step["run"]


def test_promote_moves_release_only_and_makes_the_operator_name_the_commit():
    # There is one tier to promote TO, so it is not an input: `main` is where a promotion
    # comes FROM - the demo course org runs it, refreshed by the merge itself.
    #
    # And what to promote has NO default. `main` as a default would ship whatever its tip
    # happened to be at the moment of the click, which is not necessarily the commit
    # anybody inspected on the demo org.
    doc = SHIPPED_WORKFLOWS[".github/workflows/promote.yml"]
    trigger = doc.get("on", doc.get(True))
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert "to" not in inputs
    assert inputs["ref"]["required"] is True
    assert "default" not in inputs["ref"]
    assert _fast_forward_step()["env"]["TIER"] == "release"
    # And an empty one is refused rather than falling back to a ref.
    assert 'echo "::error::name what to promote' in _fast_forward_step()["run"]


def test_promote_is_gated_on_write_here_and_nothing_else():
    # The `release` environment's required reviewer was the gate back when a promotion was
    # the first time code ran in any org. It no longer is - main is live in the demo org -
    # so the deliberate act is the press, and the review happened on the PR.
    assert "environment" not in _promote_job()


def test_promote_names_the_demo_org_inspection_as_its_gate():
    # Nothing in the workflow enforces the gate, because the gate is a person having read
    # what the demo org actually did with this code - so the form itself has to say so to
    # whoever is about to press the button.
    doc = SHIPPED_WORKFLOWS[".github/workflows/promote.yml"]
    trigger = doc.get("on", doc.get(True))
    description = trigger["workflow_dispatch"]["inputs"]["ref"]["description"]
    assert "inspection of the demo course org" in description


def test_promote_lists_what_it_will_ship_before_it_ships_it():
    # A listing printed after the push tells whoever is watching what already happened.
    run = _fast_forward_step()["run"]
    listing = run.index("Commits this puts on")
    push = run.index("git push --force-with-lease")
    assert listing < push
    # And into the run log, not only the job summary, which is written at the end.
    assert 'tee -a "$GITHUB_STEP_SUMMARY"' in run


def test_promote_can_only_fast_forward_release_along_main():
    # The two guards that make this workflow unable to ship what main has not seen, and
    # unable to rewrite the tier. Without the first, a commit off a fork could be pushed
    # to `release`; without the second, a promotion could take `release` backwards.
    run = _fast_forward_step()["run"]
    assert 'git merge-base --is-ancestor "$new" origin/main' in run
    assert 'git merge-base --is-ancestor "$tip" "$new"' in run
    assert '--force-with-lease="refs/heads/$TIER:$tip"' in run


FAN_OUT = "./.github/workflows/refresh-tier.yml"


def _fan_out_job() -> dict:
    return SHIPPED_WORKFLOWS[".github/workflows/refresh-tier.yml"]["jobs"][
        "refresh-orgs"
    ]


def _refresh_step() -> dict:
    return next(
        s
        for s in _fan_out_job()["steps"]
        if s.get("name") == "Refresh every org on this tier"
    )


def _caller(workflow: str) -> dict:
    return SHIPPED_WORKFLOWS[f".github/workflows/{workflow}"]["jobs"]["refresh-orgs"]


def test_both_tiers_deploy_through_the_same_fan_out():
    # One implementation, called twice. Two copies would drift, and the tier nobody was
    # looking at would be the one that stopped converging.
    assert _caller("promote.yml")["uses"] == FAN_OUT
    assert _caller("deploy-main.yml")["uses"] == FAN_OUT


def test_the_fan_out_refreshes_orgs_from_the_deployed_checkout():
    # It must run the refresh IN PROCESS, from the code the tier now points at.
    # Dispatching each org's own "Refresh actions" instead runs the toolkit at the ref
    # already baked into that org's workflow file, so an org whose central_ref has just
    # changed tier is re-rendered by the OLD tier's code and never converges.
    job = _fan_out_job()
    checkout = next(s for s in job["steps"] if "checkout" in s.get("uses", ""))
    assert checkout["with"]["ref"] == "${{ inputs.ref }}"
    run = _refresh_step()["run"]
    assert "python3 -m dsl_course.seed refresh --course-org" in run
    assert "gh workflow run refresh-actions.yml" not in run


def test_promote_fans_out_over_release_at_the_commit_it_pushed():
    # The promoted commit, not the ref the run was dispatched from.
    caller = _caller("promote.yml")
    assert caller["with"] == {
        "tier": "release",
        "ref": "${{ needs.promote.outputs.sha }}",
    }
    assert caller["needs"] == "promote"


def test_a_merge_to_main_fans_out_over_the_main_tier():
    # A merge to main IS the deploy to the demo course org, which declares
    # `central_ref: main` - so the shapes land with the engine instead of waiting for the
    # org's own 05:27 cron.
    doc = SHIPPED_WORKFLOWS[".github/workflows/deploy-main.yml"]
    trigger = doc.get("on", doc.get(True))
    assert trigger == {"push": {"branches": ["main"]}}
    assert doc["concurrency"] == {"group": "deploy-main", "cancel-in-progress": False}
    assert _caller("deploy-main.yml")["with"] == {
        "tier": "main",
        "ref": "${{ github.sha }}",
    }


needs_a_runner_shell = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="the fan-out is shell: it needs the bash and jq a runner has",
)


def _run_fan_out(tmp_path: Path, inventory, tier: str = "main"):
    """Execute the fan-out's own `run:` script, with `python3` stubbed.

    Grepping the script for the guards it should contain proved only that the words were
    there. What matters here is which BRANCH a given inventory takes - green with a
    warning, or red - and a branch is only ever proved by taking it. The stub answers
    `list_orgs` with `inventory` and reports every `seed refresh` as a success, so what is
    under test is the shell and nothing else.

    Returns (exit code, everything the step printed, what it wrote to the job summary).
    """
    text = inventory if isinstance(inventory, str) else json.dumps(inventory)
    (tmp_path / "inventory.json").write_text(text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    stub.write_text(
        f"""#!/bin/sh
case "$*" in
  *list_orgs*) cat {tmp_path / "inventory.json"} ;;
  *seed*refresh*) echo "refreshed: $*" ;;
  *) echo "the fan-out called something unexpected: $*" >&2; exit 99 ;;
esac
"""
    )
    stub.chmod(0o755)
    script = tmp_path / "fan-out.sh"
    script.write_text(_refresh_step()["run"])
    summary = tmp_path / "summary.md"
    summary.touch()
    done = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "TIER": tier,
            "GITHUB_STEP_SUMMARY": str(summary),
            "GH_TOKEN": "stub",
            "DSL_BOT_TOKEN": "stub",
        },
    )
    return done.returncode, done.stdout + done.stderr, summary.read_text()


def _org(name: str, **extra) -> dict:
    return {"org": name, "readable": True, "central_ref": "release", **extra}


@needs_a_runner_shell
def test_the_fan_out_is_green_when_no_org_runs_the_tier(tmp_path):
    # Every merge to main runs this, and nothing was on `main` until the demo course was
    # moved there - an empty selection has to be a run that says so, not one that fails.
    code, out, summary = _run_fan_out(tmp_path, {"course_orgs": [_org("Live")]})
    assert code == 0, out
    assert "_No course org runs `main`._" in summary
    assert "refreshed:" not in out


@needs_a_runner_shell
def test_one_orgs_retired_tier_does_not_red_every_merge(tmp_path):
    # A readable file declaring something that is not a tier is that org's own bug, and it
    # is not on this tier by any reading - so it is warned about and the orgs that ARE on
    # the tier still deploy.
    code, out, summary = _run_fan_out(
        tmp_path,
        {
            "course_orgs": [
                _org("Demo", central_ref="main"),
                _org("Typo", central_ref=None),
            ]
        },
    )
    assert code == 0, out
    assert "::warning::Typo declares" in out
    assert "refreshed:" in out and "Demo" in out
    assert "Demo - refreshed" in summary


@needs_a_runner_shell
def test_an_unreadable_org_still_reds_the_fan_out(tmp_path):
    # The other null: nothing can say which tier that org is on, so it may well be one
    # this deploy was meant to reach.
    code, out, summary = _run_fan_out(
        tmp_path,
        {"course_orgs": [_org("Broken", readable=False, central_ref=None)]},
    )
    assert code == 1, out
    assert "could not be read" in summary


@needs_a_runner_shell
def test_an_empty_inventory_fails_the_fan_out(tmp_path):
    # `jq` over an empty capture answers "no orgs" for every selection, so this used to be
    # a green run reporting nothing to do - a deploy that never happened.
    code, out, _ = _run_fan_out(tmp_path, "")
    assert code == 1, out
    assert "::error::no inventory" in out


def test_the_fan_out_carries_both_bot_tokens():
    # seed refresh reads GH_TOKEN for the API and DSL_BOT_TOKEN to propagate the repo
    # secret (ghcli.bot_token refuses to publish a token that is only GH_TOKEN).
    env = _refresh_step()["env"]
    assert env["GH_TOKEN"] == "${{ secrets.DSL_BOT_TOKEN }}"
    assert env["DSL_BOT_TOKEN"] == "${{ secrets.DSL_BOT_TOKEN }}"
    # Passed explicitly by each caller rather than inherited wholesale.
    for workflow in ("promote.yml", "deploy-main.yml"):
        assert _caller(workflow)["secrets"] == {
            "DSL_BOT_TOKEN": "${{ secrets.DSL_BOT_TOKEN }}"
        }


def test_bootstrap_org_offers_the_two_tiers_and_defaults_to_release():
    # `main` runs every merge from the moment it lands, which is the demo course org's job
    # and nobody else's - so it is offered (that org has to be bootstrappable) but it is
    # never the default a real course gets by pressing the button.
    doc = SHIPPED_WORKFLOWS[".github/workflows/bootstrap-org.yml"]
    trigger = doc.get("on", doc.get(True))
    central_ref = trigger["workflow_dispatch"]["inputs"]["central_ref"]
    assert central_ref["options"] == ["release", "main"]
    assert central_ref["default"] == "release"


def test_the_site_deploys_on_a_push_to_main_or_master():
    # A site repo has exactly one branch, whichever it was born with: the retired site
    # template made `master` ones, the scaffold makes `main`. A trigger naming only
    # `main` left every `master` site never deploying again once the sync wrote this
    # workflow over the template's.
    doc = SHIPPED_WORKFLOWS["templates/site/.github/workflows/deploy.yml"]
    trigger = doc.get("on", doc.get(True))
    assert trigger["push"]["branches"] == ["main", "master"]


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


def test_the_theme_seam_job_builds_with_the_shipped_deploy_env_and_no_more():
    # `jekyll-theme-seam` is the only place a site meets the real theme before faculty do,
    # so it proves something only while it builds the way the shipped deploy.yml does.
    # It was green at theme v2.0.0 on an env that gave the build more than a live deploy
    # gets, and every cohort site went down anyway. BASE_PATH is the sole exception: an
    # org root site's baseurl is empty, and this job has no Pages step to compute one.
    seam = SHIPPED_WORKFLOWS[".github/workflows/ci.yml"]["jobs"]["jekyll-theme-seam"]
    step = next(
        s for s in seam["steps"] if s.get("name") == "Build it against the pinned theme"
    )
    shipped = {k: v for k, v in _site_build_step()["env"].items() if k != "BASE_PATH"}
    assert step["env"] == shipped
