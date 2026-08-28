"""The inventory CLI is a pure reader: its failure modes are the discovery search and the
per-org metadata read. Both have to reach the Actions log as a line, not a traceback, and
neither may be written out as an inventory of zero (or mis-tiered) orgs.
"""

from __future__ import annotations

import json

import pytest
import yaml

from dsl_course import gh_contents, list_orgs, repos


def test_main_reports_a_failed_search_and_exits_nonzero(monkeypatch, capsys):
    def boom() -> list[dict]:
        raise RuntimeError("`gh search repos topic:dsl-course-hub` failed: HTTP 403")

    monkeypatch.setattr(list_orgs, "discover_course_orgs", boom)
    monkeypatch.setattr("sys.argv", ["list_orgs"])

    assert list_orgs.main() == 1
    assert "HTTP 403" in capsys.readouterr().err


def test_main_writes_the_inventory_when_discovery_succeeds(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        list_orgs,
        "discover_course_orgs",
        lambda: [
            {
                "org": "My-Course",
                "org_name": "My Course",
                "course_name": "Deep Learning",
                "course_code": "E1",
                "central_ref": "release",
                "url": "https://github.com/My-Course",
            }
        ],
    )
    monkeypatch.setattr(list_orgs, "discover_cohort_orgs", list)
    monkeypatch.setattr(list_orgs, "_registered_cohorts", lambda org: [])
    page = tmp_path / "inventory.md"
    monkeypatch.setattr("sys.argv", ["list_orgs", "--update-file", str(page)])

    assert list_orgs.main() == 0
    assert "My-Course" in page.read_text()
    assert "1 course orgs" in capsys.readouterr().out


def test_the_tree_nests_each_cohort_under_its_own_course_org(monkeypatch):
    monkeypatch.setattr(
        list_orgs, "_registered_cohorts", lambda org: ["C1-f2025", "C1-f2026"]
    )
    orgs = [
        {
            "org": "C1",
            "course_name": "Deep Learning",
            "course_code": "E1",
            "central_ref": "staging",
            "url": "u1",
        },
        {
            "org": "C2",
            "course_name": "Stats",
            "course_code": "",
            "central_ref": "release",
            "url": "u2",
        },
    ]
    cohorts = [
        {"org": "C1-f2025", "course": "C1", "url": "u3"},
        {"org": "C1-f2026", "course": "C1", "url": "u4"},
    ]
    out = list_orgs.render_tree(orgs, cohorts)
    assert out == (
        # the tier each course runs, so a promotion can be aimed without a second page
        "- **[C1](u1)** - Deep Learning - E1 - toolkit `staging`\n"
        "    - [C1-f2025](u3)\n"
        "    - [C1-f2026](u4)\n"
        # a course org running nothing says so, rather than being an absence
        "- **[C2](u2)** - Stats - toolkit `release`\n"
        "    - _no cohorts yet_"
    )


def test_a_live_but_unregistered_cohort_is_marked_on_the_tree(monkeypatch):
    # It exists and is tagged, but its course's registry does not list it - so every
    # nightly sync fans out past it and does NOTHING, the one failure mode that reports
    # itself nowhere else. Marked, never auto-registered: absence can be deliberate.
    monkeypatch.setattr(list_orgs, "_registered_cohorts", lambda org: ["C1-f2025"])
    out = list_orgs.render_tree(
        [
            {
                "org": "C1",
                "course_name": "DL",
                "course_code": "",
                "central_ref": "release",
                "url": "u1",
            }
        ],
        [
            {"org": "C1-f2025", "course": "C1", "url": "u2"},
            {"org": "C1-f2026", "course": "C1", "url": "u3"},
        ],
    )
    assert "    - [C1-f2025](u2)\n" in out + "\n"  # registered: no marker
    assert "    - [C1-f2026](u3) - **not registered**" in out


def test_a_cohort_pointing_at_no_discovered_course_org_is_listed_as_orphaned(
    monkeypatch,
):
    monkeypatch.setattr(list_orgs, "_registered_cohorts", lambda org: [])
    # Its `course:` pointer is dangling, or that org lost its dsl-course-hub topic. It
    # nests nowhere, and dropping it silently is how a broken pointer stays broken.
    out = list_orgs.render_tree(
        [
            {
                "org": "C1",
                "course_name": "",
                "course_code": "",
                "central_ref": "release",
                "url": "u1",
            }
        ],
        [
            {"org": "lost-f2025", "course": "deleted-course", "url": "u2"},
            {"org": "bare-f2025", "course": "", "url": "u3"},
        ],
    )
    assert "Orphaned cohort orgs" in out
    assert "[lost-f2025](u2) -> `deleted-course`" in out
    assert "[bare-f2025](u3) -> `no course: pointer`" in out


def test_metadata_is_empty_only_for_an_org_that_carries_none(monkeypatch):
    # The tier split reads this file (a `course:` pointer means COHORT), so {} from a
    # transient failure used to list a cohort org under Course orgs. Only a 404 is {}.
    monkeypatch.setattr(
        gh_contents, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)")
    )
    assert list_orgs._fetch_metadata("Cohort-f2026") == {}
    monkeypatch.setattr(
        gh_contents, "gh", lambda *a, **k: (1, "gh: HTTP 403 - forbidden")
    )
    with pytest.raises(RuntimeError, match="Cohort-f2026/.github/dsl-course.yml"):
        list_orgs._fetch_metadata("Cohort-f2026")


def test_a_malformed_dsl_course_yml_is_not_read_as_no_metadata(monkeypatch):
    # `except Exception: return {}` turned unparseable YAML into "this org declares
    # nothing", which files a cohort under Course orgs and rewrites the whole inventory
    # around it - the wrong refresh this reader exists to avoid.
    monkeypatch.setattr(gh_contents, "gh", lambda *a, **k: (0, "course: [unclosed\n"))
    with pytest.raises(yaml.YAMLError):
        list_orgs._fetch_metadata("Cohort-f2026")
    monkeypatch.setattr(
        gh_contents, "gh", lambda *a, **k: (0, "- a list, not a mapping\n")
    )
    with pytest.raises(RuntimeError, match="not a YAML mapping"):
        list_orgs._fetch_metadata("Cohort-f2026")


def test_a_full_search_page_is_read_as_truncation(monkeypatch):
    # `gh search repos --limit N` returns one page. A result set that exactly fills it is
    # indistinguishable from a truncated one, and this page is fully generated and merged
    # unattended - so every org past the limit would be silently deleted from the
    # inventory. Fail the run instead.
    monkeypatch.setattr(
        list_orgs,
        "gh_json",
        lambda *a: [
            {"name": ".github", "owner": {"login": f"Org-{i}"}}
            for i in range(list_orgs.SEARCH_LIMIT)
        ],
    )
    with pytest.raises(RuntimeError, match="truncated"):
        list_orgs._tagged_orgs(list_orgs.COURSE_HUB_TOPIC)


def test_a_partial_search_page_is_read_normally(monkeypatch):
    monkeypatch.setattr(
        list_orgs,
        "gh_json",
        lambda *a: [
            {"name": ".github", "owner": {"login": "Org-A"}},
            {"name": "course-materials", "owner": {"login": "Org-B"}},  # not a .github
        ],
    )
    monkeypatch.setattr(list_orgs, "org_exists", lambda org: True)
    assert list_orgs._tagged_orgs(list_orgs.COURSE_HUB_TOPIC) == ["Org-A"]


def test_a_deleted_org_the_search_index_still_returns_is_dropped(monkeypatch):
    # The topic search lags org deletion - a deleted org kept coming back from it for ten
    # days - so a hit is not evidence the org is there.
    monkeypatch.setattr(
        list_orgs,
        "gh_json",
        lambda *a: [
            {"name": ".github", "owner": {"login": "Live-Org"}},
            {"name": ".github", "owner": {"login": "Deleted-Org"}},
        ],
    )
    monkeypatch.setattr(list_orgs, "org_exists", lambda org: org == "Live-Org")
    assert list_orgs._tagged_orgs(list_orgs.COHORT_TOPIC) == ["Live-Org"]


def test_only_a_404_counts_as_a_deleted_org(monkeypatch):
    # Fails closed: "could not tell" must never read as "deleted", or one rate-limited
    # call drops a live org from a page that is written out whole.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, "Some-Org"))
    assert repos.org_exists("Some-Org") is True
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert repos.org_exists("Some-Org") is False
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 403 rate limit"))
    with pytest.raises(RuntimeError, match="whether the org `Some-Org` still exists"):
        repos.org_exists("Some-Org")


def test_metadata_parses_the_yaml_body(monkeypatch):
    monkeypatch.setattr(gh_contents, "gh", lambda *a, **k: (0, "course: My-Course\n"))
    assert list_orgs._fetch_metadata("Cohort-f2026") == {"course": "My-Course"}


def test_a_failed_metadata_read_stops_the_inventory_being_rewritten(
    monkeypatch, tmp_path, capsys
):
    # The page is fully generated and overwrites whatever is there: a wrong refresh is
    # worse than no refresh, so the CLI must exit 1 with the file untouched.
    monkeypatch.setattr(list_orgs, "_tagged_orgs", lambda topic: ["Cohort-f2026"])
    monkeypatch.setattr(gh_contents, "gh", lambda *a, **k: (1, "gh: HTTP 502"))
    page = tmp_path / "inventory.md"
    page.write_text("# the previous, good inventory\n")
    monkeypatch.setattr("sys.argv", ["list_orgs", "--update-file", str(page)])

    assert list_orgs.main() == 1
    assert page.read_text() == "# the previous, good inventory\n"
    assert "HTTP 502" in capsys.readouterr().err


def test_each_course_org_reports_the_toolkit_tier_it_runs(monkeypatch):
    # The inventory is where a maintainer checks what a promotion would move, so the tier
    # has to come off the same metadata read the page already makes - and an org that
    # declares nothing reports the default rather than a blank.
    monkeypatch.setattr(list_orgs, "_tagged_orgs", lambda topic: ["Soak", "Live"])
    monkeypatch.setattr(
        list_orgs,
        "_fetch_metadata",
        lambda org: {"central_ref": "staging"} if org == "Soak" else {},
    )

    assert [(o["org"], o["central_ref"]) for o in list_orgs.discover_course_orgs()] == [
        ("Live", "release"),
        ("Soak", "staging"),
    ]


def test_one_unreadable_org_does_not_hide_every_other_one(monkeypatch, capsys):
    # Promote's fan-out reads this listing to decide which orgs to refresh. It used to
    # abort on the first malformed dsl-course.yml, so one org's typo left the whole
    # estate un-refreshed. The bad org comes back with a null tier instead - which the
    # tier filter cannot match, so it is skipped and the others still go.
    monkeypatch.setattr(list_orgs, "_tagged_orgs", lambda topic: ["Bad", "Good"])

    def meta(org):
        if org == "Bad":
            raise RuntimeError("Bad/.github/dsl-course.yml is not a YAML mapping")
        return {"central_ref": "staging"}

    monkeypatch.setattr(list_orgs, "_fetch_metadata", meta)

    orgs = list_orgs.discover_course_orgs()
    assert [(o["org"], o["central_ref"]) for o in orgs] == [
        ("Bad", None),
        ("Good", "staging"),
    ]
    assert list_orgs.unreadable(orgs, []) == ["Bad"]
    assert "Bad" in capsys.readouterr().err


def test_an_unreadable_org_is_shown_on_the_tree_not_dropped_from_it(monkeypatch):
    # An org missing from this page reads as "never bootstrapped, or deleted". Saying
    # the file could not be read is the whole point of noticing.
    monkeypatch.setattr(list_orgs, "discover_cohorts", lambda org: [])
    out = list_orgs.render_tree(
        [
            {
                "org": "Bad",
                "org_name": "Bad",
                "course_name": "",
                "course_code": "",
                "central_ref": None,
                "url": "https://github.com/Bad",
            }
        ],
        [
            {
                "org": "Loose-f2026",
                "course": None,
                "url": "https://github.com/Loose-f2026",
            }
        ],
    )
    assert "**dsl-course.yml unreadable**" in out
    assert "Loose-f2026" in out


def test_an_unreadable_org_still_stops_the_inventory_being_rewritten(
    monkeypatch, tmp_path
):
    # Localising the failure must not quietly downgrade the page's own guarantee: it is
    # fully generated and merged unattended, so a partial listing is never written.
    monkeypatch.setattr(list_orgs, "_tagged_orgs", lambda topic: ["Bad"])
    monkeypatch.setattr(
        list_orgs,
        "_fetch_metadata",
        lambda org: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    page = tmp_path / "inventory.md"
    page.write_text("# the previous, good inventory\n")
    monkeypatch.setattr("sys.argv", ["list_orgs", "--update-file", str(page)])

    assert list_orgs.main() == 1
    assert page.read_text() == "# the previous, good inventory\n"


def test_the_json_form_still_prints_what_it_could_read(monkeypatch, capsys):
    # Promote parses this. It has to get the listing even when the run is partial, so the
    # verdict rides on the exit code rather than on withholding the output.
    monkeypatch.setattr(list_orgs, "_tagged_orgs", lambda topic: ["Bad"])
    monkeypatch.setattr(
        list_orgs,
        "_fetch_metadata",
        lambda org: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    monkeypatch.setattr("sys.argv", ["list_orgs"])

    assert list_orgs.main() == 1
    printed = json.loads(capsys.readouterr().out)
    assert [o["central_ref"] for o in printed["course_orgs"]] == [None]
