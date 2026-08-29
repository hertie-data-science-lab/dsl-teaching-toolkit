"""public_site.sync_public_site over a real (temp-filesystem) source repo.

The public open-courseware site must publish whatever sections the materials repo
actually HAS - `discover_sessions` is generic across every top-level section, so a course
whose content lives in `labs/` used to get empty, useless session pages. Only the gh/git
calls are faked (clone = populate a directory, commit/push = success); the copying, the
served layout and the generated `_lectures/` entries are the real code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from dsl_course import gh_contents, ghcli, public_site, site, site_repo

COURSE = "Course-Org"
SOURCE = "course-materials-f2026"
SERVED = f"public-materials/{SOURCE}"


def _seed_source(root: Path) -> None:
    """A materials repo with NO `lectures/` at all: labs + readings + a faq section,
    plus a session (3) that has no content in any section."""
    files = {
        "labs/01_first-lab/lab.ipynb": "notebook",
        "labs/01_first-lab/data/rows.csv": "a,b",  # nested - must still be published
        "labs/02_second-lab/lab.ipynb": "notebook",
        "faq/02_second-lab/faq.md": "Q: why? A: because.",
        "readings/01_first-lab/READINGS.md": "- Smith 2020, ch.1",
        "readings/01_first-lab/paper.pdf": "%PDF-1.4 copyrighted",
        "README.md": "# materials",  # not a section
        # What a faculty member keeps beside the lab, and the public must never see.
        "labs/01_first-lab/solution/answers.ipynb": "the answers",
        "labs/01_first-lab/grading.yml": "points: 10",
        "labs/02_second-lab/tests/test_hidden.py": "assert secret()",
        "readings/01_first-lab/.env.local": "OPENAI_API_KEY=sk-live",
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    # A link out of the published folder into what sits beside it. Following it would copy
    # the target's CONTENT in, under a name the denylist has no reason to refuse.
    (root / "labs/01_first-lab/handout.pdf").symlink_to("solution/answers.ipynb")


def _install_fakes(monkeypatch) -> dict[str, str]:
    """Fake the gh/git calls; return the live {path: content} of the site repo as committed."""
    committed: dict[str, str] = {}

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            spec, dest = args[2], Path(args[3])
            dest.mkdir(parents=True, exist_ok=True)
            if spec == f"{COURSE}/{SOURCE}":
                _seed_source(dest)
            else:  # the site repo, as the template leaves it
                (dest / "_config.yml").write_text(
                    'course_name: "x"\ncourse_code: "y"\ncourse_semester: "z"\n'
                )
            return (0, "")
        return (0, "")

    def fake_git(*args):
        if "add" in args:
            wd = Path(args[1])
            committed.clear()
            committed.update(
                {
                    p.relative_to(wd).as_posix(): p.read_text(errors="replace")
                    for p in wd.rglob("*")
                    if p.is_file()
                }
            )
        return (0, "")

    # `gh` and `repo_exists` are read from both namespaces: `public_site` clones the SOURCE
    # repo and `resync_public_site` looks the site repo up, while the site-repo mechanics
    # next door do the rest.
    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(site_repo, "gh", fake_gh)
    monkeypatch.setattr(site_repo, "git", fake_git)
    monkeypatch.setattr(public_site, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(site_repo, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(site_repo, "repo_is_archived", lambda org, name: False)
    monkeypatch.setattr(site_repo, "acting_login", lambda: None)
    monkeypatch.setattr(public_site, "get_file_content", lambda *a, **k: "")
    # site_repo.yaml_file now reads via gh_contents.load_yaml_config, which resolves
    # get_file_content in the UTILS namespace - stub it there too, or the real gh
    # runs (green on an authenticated dev box, red in tokenless CI).
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: "")
    monkeypatch.setattr(
        public_site, "discover_sessions", lambda org, repo: ["1", "2", "3"]
    )
    return committed


@pytest.fixture
def published(monkeypatch):
    """Run sync_public_site and return {path: content} of the site repo as committed."""
    committed = _install_fakes(monkeypatch)

    def run(**kwargs) -> dict[str, str]:
        assert public_site.sync_public_site(COURSE, SOURCE, **kwargs) == 0
        return dict(committed)

    return run


def test_publishes_every_discovered_section_not_just_lectures(published):
    files = published(readings_mode="none")
    # labs/ and faq/ are hosted and linked, though neither is named "lectures"
    assert f"{SERVED}/session-1/labs/lab.ipynb" in files
    assert f"{SERVED}/session-1/labs/data/rows.csv" in files  # nested file too
    assert f"{SERVED}/session-2/faq/faq.md" in files
    lab1 = files["_lectures/lab-01.md"]
    assert 'name: "lab - lab.ipynb"' in lab1
    # A nested file is still SERVED (asserted above) but not listed: a rendered deck's
    # assets have to stay reachable at their own paths, and linking each of them is what
    # put 1,641 links on a live site. Jekyll serves no directory index, so - unlike the
    # cohort site - there is no folder link to offer here either.
    assert 'name: "lab - data/rows.csv"' not in lab1
    assert 'name: "faq - faq.md"' in files["_lectures/session-02.md"]


def test_labs_are_their_own_rows_not_part_of_the_session_row(published):
    # As on the cohort site: `type: lab` is what the theme's labs page selects on, and a
    # lab linked from the session row too would appear twice.
    files = published(readings_mode="none")
    assert "type: lab" in files["_lectures/lab-02.md"]
    assert 'title: "Lab 2"' in files["_lectures/lab-02.md"]
    session2 = files["_lectures/session-02.md"]  # session 2 also has a faq section
    assert "type: lecture" in session2
    assert "lab - " not in session2
    # session 1 has ONLY labs (and readings, off here) - so no lecture row at all
    assert "_lectures/session-01.md" not in files


def test_session_with_no_content_gets_no_page(published):
    files = published(readings_mode="none")
    assert "_lectures/lab-01.md" in files
    assert "_lectures/session-02.md" in files
    assert "_lectures/session-03.md" not in files  # session 3 has nothing anywhere
    assert "_lectures/lab-03.md" not in files


def test_reading_list_mode_publishes_citations_as_text_only(published):
    files = published(readings_mode="reading-list")
    s1 = files["_lectures/session-01.md"]
    assert "reading_list: |" in s1 and "Smith 2020" in s1
    assert "- paper.pdf" in s1  # named, not hosted
    assert not [p for p in files if "/readings/" in p]  # no reading bytes served


def test_actual_readings_mode_hosts_and_links_readings(published):
    files = published(readings_mode="actual-readings")
    assert f"{SERVED}/session-1/readings/paper.pdf" in files
    s1 = files["_lectures/session-01.md"]
    assert 'name: "reading - paper.pdf"' in s1
    assert "### Reading list" not in s1  # hosted instead of inlined
    assert "github.com" not in s1  # public links are always site-relative


def test_readings_only_when_file_sections_are_off(published):
    files = published(readings_mode="actual-readings", include_lectures=False)
    assert f"{SERVED}/session-1/readings/paper.pdf" in files
    assert not [p for p in files if "/labs/" in p or "/faq/" in p]
    # session 2 has labs + faq but no readings -> nothing to publish for it
    assert "_lectures/session-02.md" not in files


def test_people_are_written_even_when_the_clone_has_no_data_dir(published):
    # People ride in the plan's `files` like any other tracked file, so the write must
    # create `_data/` itself - the site template does not necessarily ship one.
    files = published(readings_mode="none")
    assert "_data/people.yml" in files
    assert "instructors:" in files["_data/people.yml"]


def test_an_archived_site_repo_is_a_quiet_skip_not_a_daily_failure(monkeypatch, capsys):
    # A past cohort's site repo is frozen read-only. The clone and the commit both succeed
    # and only the push 403s, so the nightly Sync site run failed on it every single day.
    committed = _install_fakes(monkeypatch)
    monkeypatch.setattr(site_repo, "repo_is_archived", lambda org, name: True)
    assert public_site.sync_public_site(COURSE, SOURCE, "actual-readings") == 0
    assert not committed  # nothing was even cloned
    assert "is archived" in capsys.readouterr().out


def test_nothing_to_publish_at_all_is_an_error():
    # No file sections and no readings - refuse before touching a single repo.
    assert (
        public_site.sync_public_site(COURSE, SOURCE, "none", include_lectures=False)
        == 1
    )


def test_publish_persists_its_settings_in_the_site_repo(published):
    cfg = yaml.safe_load(
        published(readings_mode="actual-readings")[site_repo.PUBLISH_CONFIG]
    )
    assert cfg == {
        "source_repo": SOURCE,
        "readings_mode": "actual-readings",
        "include_lectures": True,
    }
    assert site_repo.PUBLISH_CONFIG.startswith("_")  # so Jekyll ignores it


def test_cron_resync_repeats_the_last_publishs_settings(monkeypatch):
    # Round-trip: publish once with non-default settings, then re-sync with NO arguments
    # (the cron path) and get byte-identical output - the modes came from the site repo.
    committed = _install_fakes(monkeypatch)
    assert public_site.sync_public_site(COURSE, SOURCE, "actual-readings") == 0
    persisted = dict(committed)

    monkeypatch.setattr(
        public_site, "get_file_content", lambda org, repo, path: persisted.get(path, "")
    )
    committed.clear()
    assert public_site.resync_public_site(COURSE) == 0
    assert dict(committed) == persisted


def test_cron_is_a_quiet_noop_when_the_course_never_published(monkeypatch):
    # This cron ships in every course org's .github; most never opt in. Never a failure.
    monkeypatch.setattr(public_site, "sync_public_site", lambda *a, **k: 1)
    monkeypatch.setattr(public_site, "get_file_content", lambda *a, **k: None)

    monkeypatch.setattr(public_site, "repo_exists", lambda org, name: False)
    assert public_site.resync_public_site(COURSE) == 0  # no site repo at all

    monkeypatch.setattr(public_site, "repo_exists", lambda org, name: True)
    assert public_site.resync_public_site(COURSE) == 0  # site, but nothing persisted


def test_public_sync_cli_without_source_repo_is_the_resync_path(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["site", "public-sync", "--course-org", COURSE])
    seen: list[str] = []
    monkeypatch.setattr(site, "resync_public_site", lambda org: seen.append(org) or 0)
    assert site.main() == 0
    assert seen == [COURSE]


def test_the_public_site_never_publishes_what_sits_beside_the_material(published):
    # The public path copied every discovered session folder wholesale, so a `solution/`
    # next to the lab it answers, the `grading.yml` that marks it, the hidden `tests/` and
    # a `.env` with a live key were all published with it. No release decision made any of
    # those; they are what "copy the folder" means.
    files = published(readings_mode="actual-readings")
    assert f"{SERVED}/session-1/labs/lab.ipynb" in files  # the material still ships
    for leaked in (
        f"{SERVED}/session-1/labs/solution/answers.ipynb",
        f"{SERVED}/session-1/labs/grading.yml",
        f"{SERVED}/session-2/labs/tests/test_hidden.py",
        f"{SERVED}/session-1/readings/.env.local",
    ):
        assert leaked not in files
    assert "answers" not in files["_lectures/lab-01.md"]


def test_a_symlink_cannot_smuggle_a_denied_file_onto_the_public_site(published):
    # The denylist filters NAMES, so a link is only ever as safe as the copy that follows
    # it: `handout.pdf -> solution/answers.ipynb` is a name nothing would refuse, and the
    # copy used to resolve it and publish the answers themselves. Copied AS a link, it
    # points at a folder that was never copied, and so carries nothing.
    files = published(readings_mode="actual-readings")
    assert f"{SERVED}/session-1/labs/lab.ipynb" in files
    assert "the answers" not in files.get(f"{SERVED}/session-1/labs/handout.pdf", "")
