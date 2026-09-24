"""The institution policy: the shipped defaults, an override merged over them, and a policy
that does not validate refused whole."""

from __future__ import annotations

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
        "term",
        "archive",
        "drop-in",
        "other",
    ]
    assert shipped["licences"][0]["name"] == "CC BY-NC-SA 4.0"
    assert "@" in shipped["contact"]


def test_the_toolkit_repo_carries_no_override():
    """The checkout runs on the shipped values, so `load` is the default file."""
    assert not policy.OVERRIDE_PATH.exists()
    assert policy.load() == policy.read(override=None)


def test_an_override_merges_defaults_and_replaces_lists(tmp_path):
    kinds = [
        {"key": k, "label": k.title(), "colour": "#000000", "background": "#ffffff"}
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
