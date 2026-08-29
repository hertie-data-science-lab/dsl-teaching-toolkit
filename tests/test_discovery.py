"""Discovery reads a live org over the API and decides what is content, what is
machinery, and where the session folders are.

Getting it wrong is expensive and silent: a truncated repo listing loses release targets,
a mis-classified repo hands the org-admin token to a public site repo, and a drifting
session-folder rule makes the API side disagree with the local-checkout side. No network -
`gh` / the tree fetch are stubbed.
"""

from __future__ import annotations

import pytest
import yaml

from dsl_course import discovery, seed, utils

INFRA_AND_CONTENT = [
    {"name": ".github", "topics": []},
    {"name": "welcome", "topics": []},
    {"name": "classroom-config", "topics": []},
    {"name": "my-course-f2026.github.io", "topics": []},  # the generated site repo
    {"name": "grades-alice", "topics": ["gradebook"]},  # private student gradebook
    {"name": "assignment-1-f2026-alice", "topics": ["submission"]},
    {"name": "assignment-1-f2026-template", "topics": ["assignment-template"]},
    {"name": "course-materials-f2026", "topics": []},
    {"name": "labs", "topics": ["teaching"]},
]


def test_a_gradebook_or_submission_repo_is_recognised_by_name_too():
    # The topic is stamped in a separate call after the create and never converged, so a
    # failed PATCH must not put `grades-<handle>` or `<slug>-<handle>` on a public page.
    repos = [
        {"name": "assignment-1", "isTemplate": True, "topics": []},
        {"name": "assignment-1-ada-l", "isTemplate": False, "topics": []},
        {"name": "grades-ada-l", "topics": []},
        {"name": "materials", "topics": []},
    ]
    assert discovery.has_infra_topic({"name": "grades-ada-l", "topics": []})
    assert discovery.is_student_repo(repos[1], repos)
    assert discovery.is_student_repo(repos[2], repos)
    assert not discovery.is_student_repo(repos[3], repos)
    assert not discovery.is_student_repo(repos[0], repos)  # the template itself


def test_is_infra_repo_excludes_by_name_and_by_topic():
    infra, content = INFRA_AND_CONTENT[:7], INFRA_AND_CONTENT[7:]
    assert all(discovery._is_infra_repo(r) for r in infra)
    assert not any(discovery._is_infra_repo(r) for r in content)
    assert not discovery._is_infra_repo({"name": "notes"})  # topics absent -> content


def test_unregister_cohort_rewrites_the_registry_without_it(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "get_file_content",
        lambda *a: "cohorts:\n- Cohort-f2025\n- Cohort-f2026\n",
    )
    written: list = []
    monkeypatch.setattr(discovery, "put_file", lambda *a: written.append(a) or True)

    assert discovery.unregister_cohort("Course-Org", "Cohort-f2025") is True
    (_org, _repo, _path, body, message) = written[0]
    assert yaml.safe_load(body.decode()) == {"cohorts": ["Cohort-f2026"]}
    assert "Cohort-f2025" in message


def test_unregister_cohort_writes_nothing_for_one_already_absent(monkeypatch):
    # Idempotent: the nightly refresh probes every cohort every night, and a no-op must
    # not put a commit into the course org's .github repo each time.
    monkeypatch.setattr(
        discovery, "get_file_content", lambda *a: "cohorts:\n- Cohort-f2026\n"
    )
    monkeypatch.setattr(
        discovery, "put_file", lambda *a: pytest.fail("wrote for an absent cohort")
    )
    assert discovery.unregister_cohort("Course-Org", "Cohort-f2025") is True


def test_handed_out_assignments_are_the_topic_stamped_cohort_templates(monkeypatch):
    # The site withholds an assignment's brief until this says the cohort has it, so it
    # must name the frozen cohort template (assign.py stage 1) and nothing else - not the
    # per-student submission repos beside it, not the gradebooks.
    monkeypatch.setattr(discovery, "list_org_repos", lambda org: INFRA_AND_CONTENT)
    assert discovery.discover_handed_out_assignments("Cohort-f2026") == frozenset(
        {"assignment-1-f2026-template"}
    )


def test_both_discover_functions_apply_the_same_infra_exclusions(monkeypatch):
    # One shared predicate: the public <org>.github.io site repo must never be treated
    # as a content repo (those HOST the faculty workflows and get DSL_BOT_TOKEN set as a
    # repo secret), and gradebooks/submissions must never appear as release targets.
    monkeypatch.setattr(discovery, "list_org_repos", lambda org: INFRA_AND_CONTENT)
    expected = ["course-materials-f2026", "labs"]
    assert discovery.discover_cohort_repos(["Cohort-f2026"]) == expected
    assert discovery.discover_content_repos("My-Course-E1234") == expected


def test_discover_content_repos_also_excludes_assignment_templates_by_name(monkeypatch):
    # Course-org assignment templates carry no `assignment-template` topic (that one is
    # set on the frozen cohort-side copy), so the name prefix is the content-side rule.
    monkeypatch.setattr(
        discovery,
        "list_org_repos",
        lambda org: [
            {"name": "assignment-1-f2026", "topics": ["assignment"]},
            {"name": "course-materials-f2026", "topics": []},
        ],
    )
    assert discovery.discover_content_repos("My-Course-E1234") == [
        "course-materials-f2026"
    ]


def test_list_org_repos_paginates_instead_of_capping(monkeypatch):
    # A cohort org holds a repo per student per assignment plus a gradebook each, so any
    # fixed --limit silently truncates discovery. --paginate walks every page, and each
    # page's --jq output is NDJSON (not one concatenated array).
    calls = []
    pages = (
        '{"name":"a","topics":[],"isTemplate":false}\n'
        '{"name":"b","topics":[],"isTemplate":true}\n'
    )
    monkeypatch.setattr(
        discovery, "gh", lambda *args: (calls.append(args), (0, pages))[1]
    )
    assert [r["name"] for r in discovery.list_org_repos("Org")] == ["a", "b"]
    assert "--paginate" in calls[0] and "--limit" not in calls[0]
    assert "orgs/Org/repos?per_page=100" in calls[0]


def test_list_org_repos_raises_instead_of_reporting_an_empty_org(monkeypatch):
    # [] means the org really is empty. A failed listing used to look identical, so a
    # transient API error made Refresh converge "0 content repo(s)" and go green, and
    # made profile_readme file a cohort org (no `welcome` found) as a course org.
    monkeypatch.setattr(discovery, "gh", lambda *args: (1, "gh: HTTP 502"))
    with pytest.raises(RuntimeError, match="could not list repos in Org"):
        discovery.list_org_repos("Org")
    monkeypatch.setattr(discovery, "gh", lambda *args: (0, "not json\n"))
    with pytest.raises(RuntimeError, match="unparseable repo listing"):
        discovery.list_org_repos("Org")


def test_list_org_repos_reports_a_genuinely_empty_org_as_empty(monkeypatch):
    monkeypatch.setattr(discovery, "gh", lambda *args: (0, ""))
    assert discovery.list_org_repos("Org") == []


TREES = {
    "labs": ["01_intro", "02_functions", "materials/01_intro", "readings"],
    "lectures": ["01_intro"],
}


def test_discover_release_sources_detects_root_and_nested_shapes(monkeypatch):
    # root shape: a deploy landed with no cohort_dest_path, so session folders sit directly at
    # the dest repo's root (labs/lectures in a live course).
    # nested shape: a deploy routed its session folders under a shared repo's subfolder.
    monkeypatch.setattr(discovery, "_repo_tree_dirs", lambda org, repo: TREES[repo])
    sources = discovery.discover_release_sources("org", ["labs", "lectures"])
    assert set(sources) == {
        ("labs", "", "01_intro", 1),
        ("labs", "", "02_functions", 2),
        ("labs", "materials", "01_intro", 1),
        ("lectures", "", "01_intro", 1),
    }


def test_sections_and_sessions_ignore_root_level_and_over_deep_folders(monkeypatch):
    # Only `section/NN_.../` makes a section: a bare `NN_.../` at the root has no
    # section name, and anything deeper is a session's own contents.
    monkeypatch.setattr(
        discovery,
        "_repo_tree_dirs",
        lambda org, repo: [
            # Root-level session folder: excluded (no parent section). 07, not 01, so it
            # can't hide behind lectures/01_intro's session number - if the root-level
            # exclusion ever regressed, a spurious "7" would appear in the assert below.
            "07_loose",
            "lectures",
            "lectures/01_intro",
            "lectures/01_intro/data",  # too deep
            "notes/appendix",  # no ordinal prefix
            "labs/2_wrangling",
        ],
    )
    assert discovery.discover_sessions("org", "r") == ["1", "2"]


def test_api_and_filesystem_transports_share_one_session_folder_rule(tmp_path):
    # utils.discover_sections (local checkout, used by the public-site builder) and the API-side
    # discovery must never drift: both feed their directory listing through
    # utils.session_dirs, so the same tree yields the same sections either way.
    tree = [
        "lectures",
        "lectures/01_intro",
        "labs",
        "labs/03_wrangling",
        "notes",
        "notes/appendix",
        "07_loose",
    ]
    for rel in tree:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    api_sections = sorted(
        {section for section, _, _ in utils.session_dirs(tree) if section}
    )
    assert utils.discover_sections(tmp_path) == api_sections == ["labs", "lectures"]


def test_repo_tree_dirs_reads_an_absent_or_empty_repo_as_no_directories(monkeypatch):
    # A 404 (no such repo/tree) and a 409 (a repo with no commits yet) both genuinely mean
    # "no session folders" - a brand-new cohort repo is not a failure.
    monkeypatch.setattr(discovery, "get_default_branch", lambda org, repo: "main")
    for out in ("gh: Not Found (HTTP 404)", "gh: Conflict (HTTP 409)"):
        monkeypatch.setattr(utils, "gh", lambda *a, out=out, **k: (1, out))
        assert discovery._repo_tree_dirs("Cohort-f2026", "materials") == ()


def test_repo_tree_dirs_raises_rather_than_reporting_a_repo_with_no_sessions(
    monkeypatch,
):
    # The site-wipe class: these rows ARE the cohort site's schedule, and the sync clears
    # and rewrites the collections from them - so a rate-limited tree fetch swallowed as
    # `[]` republished the site with every session row deleted, silently and green.
    monkeypatch.setattr(discovery, "get_default_branch", lambda org, repo: "main")
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: HTTP 502 Bad Gateway"))
    with pytest.raises(RuntimeError, match="could not read the file tree"):
        discovery.discover_release_sources("Cohort-f2026", ["materials"])


def test_both_transports_share_one_tree_fetch(monkeypatch):
    # discovery filters the directories, site filters the blobs, but the fetch - and its
    # absent-vs-failed discrimination - is one helper, so the two can't drift apart again.
    from dsl_course import site

    calls = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return (0, "false\nlectures\nlectures/01_intro\n")

    monkeypatch.setattr(discovery, "get_default_branch", lambda org, repo: "main")
    monkeypatch.setattr(site, "get_default_branch", lambda org, repo: "main")
    monkeypatch.setattr(utils, "gh", fake_gh)
    discovery._repo_tree_dirs("Cohort-f2026", "materials")
    site._repo_tree("Cohort-f2026", "materials")
    assert [a[-1] for a in calls] == [
        '"\\(.truncated)", (.tree[] | select(.type=="tree") | .path)',
        '"\\(.truncated)", (.tree[] | select(.type=="blob") | .path)',
    ]


def _registry(text: str | None):
    return lambda *a, **k: text


def test_read_cohorts_returns_empty_for_an_absent_registry(monkeypatch):
    monkeypatch.setattr(discovery, "get_file_content", _registry(None))
    assert discovery._read_cohorts("Course") == []


def test_read_cohorts_reads_a_valid_registry(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "get_file_content",
        _registry("cohorts:\n  - Course-f2026\n  - Course-f2025\n"),
    )
    assert discovery.discover_cohorts("Course") == ["Course-f2025", "Course-f2026"]


def test_read_cohorts_tolerates_a_bare_list_registry(monkeypatch):
    # The machine-written form is {cohorts: [...]}, but the file is human-editable and a
    # bare top-level list has always been accepted - it must not newly hard-fail the sync.
    monkeypatch.setattr(
        discovery, "get_file_content", _registry("- Course-f2026\n- Course-f2025\n")
    )
    assert discovery.discover_cohorts("Course") == ["Course-f2025", "Course-f2026"]


def test_read_cohorts_names_the_file_when_the_yaml_does_not_parse(monkeypatch):
    # The bare safe_load surfaced a raw PyYAML traceback from wherever the registry
    # happened to be read, naming "<unicode string>" rather than the file to fix.
    monkeypatch.setattr(
        discovery, "get_file_content", _registry("cohorts: [unclosed\n")
    )
    with pytest.raises(RuntimeError, match="malformed cohort registry in Course"):
        discovery._read_cohorts("Course")


def test_read_cohorts_raises_on_a_malformed_registry_shape(monkeypatch):
    # A malformed shape used to be flattened to [], which renders every dropdown as
    # "(none-yet)" and lets a whole-course sync go quietly green. Now it raises.
    monkeypatch.setattr(
        discovery, "get_file_content", _registry("cohorts: Course-f2026\n")
    )
    with pytest.raises(RuntimeError, match="expected a list of cohort org names"):
        discovery._read_cohorts("Course")
    monkeypatch.setattr(
        discovery, "get_file_content", _registry("cohorts:\n  - 1\n  - 2\n")
    )
    with pytest.raises(RuntimeError, match="expected a list of cohort org names"):
        discovery._read_cohorts("Course")


def test_register_cohort_reports_failure_when_the_write_fails(monkeypatch):
    # The put_file return was discarded and log_ok("registered ...") fired unconditionally,
    # so bootstrap claimed a cohort was registered even when the write failed.
    monkeypatch.setattr(discovery, "_read_cohorts", lambda org: [])
    monkeypatch.setattr(discovery, "put_file", lambda *a, **k: False)
    assert discovery.register_cohort("Course", "Course-f2026") is False

    monkeypatch.setattr(discovery, "put_file", lambda *a, **k: True)
    assert discovery.register_cohort("Course", "Course-f2026") is True


def test_register_cohort_is_idempotent_when_already_registered(monkeypatch):
    monkeypatch.setattr(discovery, "_read_cohorts", lambda org: ["Course-f2026"])
    # no write attempted, still reports success (already present)
    monkeypatch.setattr(
        discovery, "put_file", lambda *a, **k: pytest.fail("should not write")
    )
    assert discovery.register_cohort("Course", "Course-f2026") is True


def test_seed_facade_still_exposes_discovery(monkeypatch):
    # site/scaffold/sync_* call these as seed.<name> - the split must be invisible.
    monkeypatch.setattr(discovery, "list_org_repos", lambda org: INFRA_AND_CONTENT)
    assert seed.discover_content_repos("Org") == ["course-materials-f2026", "labs"]
    assert seed.COHORTS_PATH == discovery.COHORTS_PATH


def test_org_tier_reads_the_dotgithub_topic_then_the_cohort_only_repos_then_gives_up():
    # None is a real answer: a legacy cohort (`.github` + student repos, no `welcome`, no
    # topics) is indistinguishable from a course org by elimination, and the faculty
    # sweep reads "course" as "push everywhere".
    gh = lambda *topics: {"name": ".github", "topics": list(topics)}
    assert discovery.org_tier([gh("dsl-cohort"), {"name": "a1-ada"}]) == "cohort"
    assert discovery.org_tier([gh("dsl-course-hub"), {"name": "cm-f2026"}]) == "course"
    assert discovery.org_tier([gh(), {"name": "welcome"}]) == "cohort"
    assert discovery.org_tier([gh(), {"name": "classroom-config"}]) == "cohort"
    assert discovery.org_tier([gh(), {"name": "assignment-1-ada"}]) is None
    assert discovery.org_tier([{"name": "materials"}]) is None  # no .github at all


def test_student_repo_names_by_topic_or_by_name():
    # The topics are stamped after the create and never converged, so a failed PATCH must
    # not let a student repo be treated as faculty-authored content.
    repos = [
        {"name": "assignment-1", "isTemplate": True, "topics": []},
        {"name": "assignment-1-ada-l", "topics": []},  # topic never landed
        {"name": "assignment-1-wizards", "topics": ["assignment-1", "submission"]},
        {"name": "grades-ada-l", "topics": []},
        {"name": "materials", "topics": []},
        {"name": "welcome", "topics": []},
    ]
    # The template itself is not a student repo unless its own topic says so.
    assert discovery.student_repo_names(repos) == {
        "assignment-1-ada-l",
        "assignment-1-wizards",
        "grades-ada-l",
    }
