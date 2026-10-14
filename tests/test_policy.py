"""The institution policy: the shipped defaults, an override merged over them, and a policy
that does not validate refused whole."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dsl_course import policy

ROOT = Path(__file__).resolve().parent.parent


def _override(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "policy.yml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_the_shipped_policy_carries_hertie_values():
    shipped = policy.read(override=None)
    assert shipped["defaults"] == {
        "late_window_days": 10,
        "late_penalty_per_day": "10%",
        "max_team_size": 5,
        "visibility": "private",
        "team_formation": "self_select",
        "marks_per_question": True,
        "formats": ["ipynb"],
        "timezone": "Europe/Berlin",
        "archive": {"grace_days": 60},
        "semester_dest_repo": "materials",
    }
    assert [k["key"] for k in shipped["kinds"]] == [
        "lecture",
        "lab",
        "readings",
        "exam",
        "assignment",
        "semester",
        "archive",
        "drop-in",
        "assets",
        "other",
    ]
    assert shipped["licences"][0]["name"] == "CC BY-NC-SA 4.0"
    assert "@" in shipped["contact"]


def test_the_shipped_default_validates():
    """`read` raises on a policy that does not validate; the shipped file is one."""
    assert policy._problems(policy.read(override=None)) == []


def test_an_override_merges_defaults_and_replaces_lists(tmp_path):
    kinds = [
        {"key": k, "plural": f"{k}s", "numbered": False, "label": k.title()}
        | {"colour": "#000000", "background": "#ffffff"}
        | {"system": k in policy.SYSTEM_KINDS}
        for k in ("seminar", *policy.SYSTEM_KINDS, policy.FALLBACK_KIND)
    ]
    path = _override(
        tmp_path,
        {
            "defaults": {"late_window_days": 3, "archive": {"grace_days": 30}},
            "institution": {"name": "Elsewhere"},
            "kinds": kinds,
        },
    )
    got = policy.read(override=path)
    assert got["defaults"]["late_window_days"] == 3
    assert got["defaults"]["late_penalty_per_day"] == "10%"
    assert got["defaults"]["archive"] == {"grace_days": 30}
    assert got["institution"]["name"] == "Elsewhere"
    assert got["institution"]["dsl_org_url"].startswith("https://")
    assert got["kinds"][0]["key"] == "seminar"


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({"defaults": {"late_penalty_per_day": "10"}}, "late_penalty_per_day"),
        ({"defaults": {"late_penalty_per_day": "150%"}}, "late_penalty_per_day"),
        ({"defaults": {"late_penalty_per_day": "100.9%"}}, "late_penalty_per_day"),
        ({"defaults": {"max_team_size": -3}}, "max_team_size"),
        ({"defaults": {"late_window_days": -10}}, "late_window_days"),
        ({"defaults": {"archive": {"grace_days": -60}}}, "grace_days"),
        ({"defaults": {"visibility": "internal"}}, "visibility"),
        ({"defaults": {"timezone": "Mars/Olympus"}}, "timezone"),
        ({"defaults": {"typo": 1}}, "typo"),
        ({"contact": "nobody"}, "contact"),
        ({"licences": []}, "licences"),
        (
            {
                "kinds": [
                    {
                        "key": "other",
                        "plural": "other",
                        "numbered": False,
                        "label": "Event",
                        "colour": "#000000",
                        "background": "#ffffff",
                        "system": False,
                    }
                ]
            },
            "`assignment` is required",
        ),
        (
            {
                "kinds": [
                    {
                        "key": k,
                        "plural": k,
                        "numbered": False,
                        "label": k,
                        "colour": "#000000",
                        "background": "#ffffff",
                        "system": True,
                    }
                    for k in (*policy.SYSTEM_KINDS, policy.FALLBACK_KIND)
                ]
            },
            "`system` is fixed",
        ),
    ],
)
def test_an_override_that_does_not_validate_is_refused(tmp_path, data, problem):
    with pytest.raises(policy.PolicyError, match=problem):
        policy.read(override=_override(tmp_path, data))


def test_the_seeded_site_config_carries_the_policy_institution_block():
    seed = yaml.safe_load((ROOT / "templates/site-seed/_config.yml").read_text())
    block = policy.read(override=None)["institution"]
    assert seed["schoolname"] == block["name"]
    assert seed["schoolurl"] == block["url"]
    assert seed["dsl_org_url"] == block["dsl_org_url"]
    assert seed["address"].strip() == block["address"].strip()


def test_every_kind_carries_its_plural_and_whether_it_is_numbered_and_shown():
    kinds = {k["key"]: k for k in policy.read(override=None)["kinds"]}
    assert kinds["lecture"]["plural"] == "lectures" and kinds["lecture"]["numbered"]
    assert kinds["drop-in"]["plural"] == "drop-ins"
    # `shown` defaults to true; supporting files are released, never a row or a tab.
    assert [k for k, v in kinds.items() if not v["shown"]] == ["assets"]


def test_the_kind_accessors_read_the_policy():
    assert policy.plural_of("lab") == "labs"
    assert policy.plural_of("readings") == "readings"
    assert policy.kind_for_plural("exams") == "exam"
    assert policy.kind_for_plural("seminars") is None
    assert policy.numbered_kinds() == ("lecture", "lab", "assignment", "drop-in")
    assert "assets" not in policy.shown_kinds()
    assert "lecture" in policy.shown_kinds() and "semester" in policy.shown_kinds()


def test_two_kinds_may_not_share_a_plural(tmp_path):
    kinds = policy.read(override=None)["kinds"]
    kinds = [dict(k, plural="lectures") if k["key"] == "lab" else k for k in kinds]
    with pytest.raises(policy.PolicyError, match="each plural may appear once"):
        policy.read(override=_override(tmp_path, {"kinds": kinds}))


def test_the_console_export_is_the_policy_the_engine_runs_on():
    exported = json.loads((ROOT / "console/schemas/policy.json").read_text())
    assert exported == policy.load()
