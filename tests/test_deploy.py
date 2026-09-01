"""deploy.parse_path_pairs turns the Release materials button's two
comma-separated inputs into (course_source_path, cohort_dest_path) pairs, and the read
grant covers both cohort role teams.

(Session-directory discovery/matching lives in course.py - see test_course.py; the deploy
batching itself is exercised in test_scheduler.py, which drives the same
`deploy_many` the button now goes through.)"""

from __future__ import annotations

import pytest

from dsl_course import access, course, deploy, ghcli, repos


def test_a_single_path_with_no_comma_still_works():
    # The overwhelmingly common case: one folder, mirrored.
    assert deploy.parse_path_pairs("lectures/02_intro") == [("lectures/02_intro", None)]
    # ...and one folder with an explicit destination.
    assert deploy.parse_path_pairs("lectures/02_intro", "week02") == [
        ("lectures/02_intro", "week02")
    ]


def test_blank_dest_path_mirrors_every_source_path():
    # Blank dest path means the same thing here as an omitted `cohort_dest_path:` in
    # schedule.yml: mirror the source path (None = let deploy_many mirror it).
    assert deploy.parse_path_pairs("lectures/02,labs/02,readings/02") == [
        ("lectures/02", None),
        ("labs/02", None),
        ("readings/02", None),
    ]


def test_equal_length_lists_are_paired_by_index():
    assert deploy.parse_path_pairs(
        "lectures/02,labs/02,readings/02", "week02/lecture,week02/lab,week02/reading"
    ) == [
        ("lectures/02", "week02/lecture"),
        ("labs/02", "week02/lab"),
        ("readings/02", "week02/reading"),
    ]


def test_mismatched_counts_fail_loudly_naming_both_counts():
    # A manual button run has an operator watching it, so a short dest list is an
    # error naming both counts - not a silently truncated release (the schedule, on an
    # unattended cron, is the one that drops what it can't pair).
    with pytest.raises(
        ValueError, match="3 course_source_paths but 2 cohort_dest_paths"
    ):
        deploy.parse_path_pairs("a,b,c", "x,y")
    with pytest.raises(
        ValueError, match="2 course_source_paths but 3 cohort_dest_paths"
    ):
        deploy.parse_path_pairs("a,b", "x,y,z")


def test_whitespace_and_trailing_commas_are_tolerated():
    # Faculty type these into a GitHub text box: spaces after commas and a stray
    # trailing comma must not change the pairing (or trip the count check).
    assert deploy.parse_path_pairs(
        " lectures/02 , labs/02 ,", "week02/lecture , week02/lab , "
    ) == [
        ("lectures/02", "week02/lecture"),
        ("labs/02", "week02/lab"),
    ]


def test_an_empty_source_path_is_an_error_not_an_empty_batch():
    with pytest.raises(ValueError, match="course-source-path is empty"):
        deploy.parse_path_pairs("  ,  ")


def test_cli_rejects_a_count_mismatch_with_a_nonzero_exit(monkeypatch, capsys):
    # End to end through the button's own entry point: no clone is attempted, and the
    # run fails visibly rather than releasing a partial batch.
    monkeypatch.setattr(
        deploy, "deploy_many", lambda *a, **k: pytest.fail("must not deploy")
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy",
            "--source-org",
            "Course",
            "--course-source-repo",
            "course-materials-f2026",
            "--cohort-org",
            "Cohort-f2026",
            "--course-source-path",
            "a,b,c",
            "--cohort-dest-path",
            "x,y",
        ],
    )
    assert deploy.main() == 1
    assert "3 course_source_paths but 2 cohort_dest_paths" in capsys.readouterr().err


def test_cli_builds_one_deploy_per_pair_and_one_batch(monkeypatch):
    # Every pair becomes a Deploy against the SAME source/dest repo, and they all go
    # through ONE deploy_many call - so each repo is cloned once for the whole batch.
    seen = {}

    def fake_deploy_many(source_org, cohort_org, deploys, sync=True):
        seen.update(
            source_org=source_org, cohort_org=cohort_org, deploys=deploys, sync=sync
        )
        return 0, True

    monkeypatch.setattr(deploy, "deploy_many", fake_deploy_many)
    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy",
            "--source-org",
            "Course",
            "--course-source-repo",
            "course-materials-f2026",
            "--cohort-org",
            "Cohort-f2026",
            "--cohort-dest-repo",
            "materials",
            "--course-source-path",
            "lectures/02,labs/02",
            "--cohort-dest-path",
            "week02/lecture,",
        ],
    )
    assert deploy.main() == 1  # unpaired counts (2 sources, 1 dest)

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy",
            "--source-org",
            "Course",
            "--course-source-repo",
            "course-materials-f2026",
            "--cohort-org",
            "Cohort-f2026",
            "--course-source-path",
            "lectures/02,labs/02",
        ],
    )
    assert deploy.main() == 0
    assert seen["source_org"] == "Course" and seen["cohort_org"] == "Cohort-f2026"
    assert [
        (
            d.course_source_repo,
            d.course_source_path,
            d.cohort_dest_repo,
            d.cohort_dest_path,
        )
        for d in seen["deploys"]
    ] == [
        ("course-materials-f2026", "lectures/02", "materials", None),
        ("course-materials-f2026", "labs/02", "materials", None),
    ]
    # The button syncs the site itself (unlike the scheduler, which batches and syncs once).
    assert seen["sync"] is True


def test_cohort_dest_repo_defaults_to_materials(monkeypatch):
    captured = []
    monkeypatch.setattr(
        deploy,
        "deploy_many",
        lambda *a, **k: (captured.append(a[2]), (0, True))[1],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy",
            "--source-org",
            "Course",
            "--course-source-repo",
            "course-materials-f2026",
            "--cohort-org",
            "Cohort-f2026",
            "--cohort-dest-repo",
            "   ",  # a blank text box must not create a repo named ""
            "--course-source-path",
            "lectures/02",
        ],
    )
    assert deploy.main() == 0
    assert captured[0][0].cohort_dest_repo == "materials"


def test_dry_run_prints_the_resolved_pairs_without_deploying(monkeypatch, capsys):
    # The human-pressed release path gets the scheduler's dry-run: print the resolved
    # source -> dest pairs and exit, cloning/copying nothing (the cheapest guard against a
    # root-path release landing somewhere unexpected).
    monkeypatch.setattr(
        deploy,
        "deploy_many",
        lambda *a, **k: pytest.fail("must not deploy on --dry-run"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy",
            "--source-org",
            "Course",
            "--course-source-repo",
            "course-materials-f2026",
            "--cohort-org",
            "Cohort-f2026",
            "--course-source-path",
            "lectures/02,labs/02",
            "--cohort-dest-path",
            "week02/lecture,week02/lab",
            "--dry-run",
        ],
    )
    assert deploy.main() == 0
    out = capsys.readouterr().out
    assert "course-materials-f2026/lectures/02 -> materials/week02/lecture" in out
    assert "course-materials-f2026/labs/02 -> materials/week02/lab" in out


def _dry_run_argv(source_path):
    return [
        "deploy",
        "--source-org",
        "Course",
        "--course-source-repo",
        "course-materials-f2026",
        "--cohort-org",
        "Cohort-f2026",
        "--course-source-path",
        source_path,
        "--dry-run",
    ]


def test_dry_run_shows_a_whole_repo_release_landing_at_the_dest_root(
    monkeypatch, capsys
):
    # The case a dry-run most needs to get right is the one that ships the most. It must
    # model deploy_many's destination rule, not a near-miss of it: a root path means the
    # dest repo's root, so printing `materials//` would misdescribe the release.
    monkeypatch.setattr(
        deploy, "deploy_many", lambda *a, **k: pytest.fail("must not deploy")
    )
    monkeypatch.setattr("sys.argv", _dry_run_argv("/"))
    assert deploy.main() == 0
    out = capsys.readouterr().out
    assert "course-materials-f2026// -> materials/(repo root)" in out
    assert "materials//" not in out


def test_dry_run_still_flags_a_path_escaping_the_clone(monkeypatch, capsys):
    # The root half of this guard retired when the root became a legal release, but the
    # escape half did not: it is still the cheapest catch, needing no clone, and it must
    # red the run rather than print a plausible-looking destination.
    monkeypatch.setattr(
        deploy, "deploy_many", lambda *a, **k: pytest.fail("must not deploy")
    )
    monkeypatch.setattr("sys.argv", _dry_run_argv("lectures/02,../../etc/passwd"))
    assert deploy.main() == 1
    out = capsys.readouterr().out
    assert "UNSAFE" in out and "escapes the clone" in out
    # the safe pair is still shown, so the operator sees the whole batch
    assert "course-materials-f2026/lectures/02 -> materials/lectures/02" in out


def test_a_released_repo_is_actually_granted_to_both_cohort_role_teams(monkeypatch):
    # Auditors see exactly what enrolled students see once it's released, so a release must
    # grant BOTH role teams read on its destination repo. Asserted through a real release
    # rather than by comparing an import binding: deploy could call the right helper for
    # the wrong repo, or not call it at all, and the binding would still match.
    granted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        access,
        "grant_team_repo_access",
        lambda org, team, repo, perm, **k: granted.append((team, repo, perm)) or True,
    )
    _stub_deploy_many(monkeypatch, _one_file, real_grants=True)
    deploy.deploy_many("COURSE", "COHORT", [_deploy("sec")], sync=False)
    assert ("students", "materials", "pull") in granted
    assert ("auditors", "materials", "pull") in granted


SUPERSEDED = "Released course materials (enrolled students only)"
CURRENT = "Released lectures, labs, readings, & other materials"


def _converge(monkeypatch, listing):
    """The PATCH calls converge_descriptions makes over `listing`, and the mutated listing."""
    calls = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(repos, "gh", fake_gh)
    monkeypatch.setattr(repos, "log", lambda *a, **k: None)
    monkeypatch.setattr(repos, "log_ok", lambda *a, **k: None)
    swept = repos.converge_descriptions("Org", listing)
    return [a for a in calls if "--method" in a and "PATCH" in a], swept.changed


def test_a_superseded_description_is_updated_and_others_are_left_alone(monkeypatch):
    # Only a wording WE have since replaced may be overwritten. Faculty's own text, and a
    # repo already carrying the current wording, must produce no request at all.
    repos = [
        {"name": "materials", "description": SUPERSEDED},
        {"name": "labs", "description": CURRENT},
        {"name": "lectures", "description": "Slides and notebooks, weeks 1-5"},
        {"name": "readings", "description": None},
    ]
    patched, changed = _converge(monkeypatch, repos)
    assert changed == 1
    assert len(patched) == 1
    assert "repos/Org/materials" in patched[0]
    # and the listing is corrected in place, so a table rendered next shows the new text
    assert repos[0]["description"] == CURRENT
    assert repos[2]["description"] == "Slides and notebooks, weeks 1-5"


def test_every_superseded_description_names_a_replacement_we_still_write(monkeypatch):
    # The forcing function: each mapping value must be a description the code actually
    # writes today, or convergence would move repos onto a wording nothing else uses.
    #
    # Every string constant in those modules, via `ast` rather than a `description="..."`
    # regex: a tier-specific description is written by a conditional expression, which the
    # regex could not see - so the two tier tables went unguarded, which is the half of
    # this rule most likely to rot (one old wording, two new ones).
    import ast
    from pathlib import Path

    literals = set()
    for mod in ("bootstrap_course", "deploy", "scaffold", "grades", "assign"):
        tree = ast.parse((Path(deploy.__file__).parent / f"{mod}.py").read_text())
        literals |= {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            if isinstance(n.value, str)
        }
    for table in (
        repos.SUPERSEDED_DESCRIPTIONS,
        repos.SUPERSEDED_COHORT_DESCRIPTIONS,
        repos.SUPERSEDED_COURSE_DESCRIPTIONS,
    ):
        for old, new in table.items():
            assert new in literals, f"{new!r} is not written anywhere any more"
            assert old not in literals, (
                f"{old!r} is still written - it is not superseded"
            )


def test_the_dotgithub_description_says_the_opposite_thing_per_tier():
    # A cohort org's .github is scaffolding nobody should open; a course org's is where
    # faculty work. Converging both onto one wording would tell half of them the wrong
    # thing, which is why the tier picks the table.
    listing = [{"name": ".github", "description": "Org profile and configuration"}]
    calls = []
    original = repos.gh

    def fake(*args, **kwargs):
        calls.append(args)
        return 0, ""

    repos.gh = fake
    try:
        cohort = [dict(r) for r in listing]
        repos.converge_descriptions("Cohort-f2026", cohort, "cohort")
        course = [dict(r) for r in listing]
        repos.converge_descriptions("Course", course, "course")
    finally:
        repos.gh = original
    assert cohort[0]["description"] == "[do not touch]: Org profile and configuration"
    assert course[0]["description"] == "[control panel]: Org profile & configuration"


# --- releasing the whole repo -------------------------------------------------------
# `/` is the "release everything" spelling. The end-to-end proof that it flows through
# clone -> copytree -> `git add` lives in test_scheduler.py; what is pinned here is the two
# decisions it rests on - which spellings mean the root, and what never travels with a copy.

# The one definition of "this means the repo root". Blank is included because that is what
# an empty `cohort_dest_path` reduces to internally; neither front door accepts it as a
# SOURCE (parse_path_pairs and schedule.py both reject an empty course_source_path).
ROOT_SPELLINGS = ["/", ".", "", "./", "//"]


@pytest.mark.parametrize("spelling", ROOT_SPELLINGS)
def test_every_spelling_of_the_repo_root_resolves_to_the_clone_root(tmp_path, spelling):
    assert deploy._resolve_within(tmp_path, spelling) == tmp_path.resolve()


@pytest.mark.parametrize("escape", ["../outside", "labs/../../outside", "/../outside"])
def test_a_path_escaping_the_clone_is_still_refused(tmp_path, escape):
    # Allowing the root must not have widened the door to paths outside it.
    assert deploy._resolve_within(tmp_path, escape) is None


def test_a_normal_subpath_still_resolves_under_the_clone(tmp_path):
    (tmp_path / "labs").mkdir()
    assert deploy._resolve_within(tmp_path, "labs/") == (tmp_path / "labs").resolve()


def test_a_whole_repo_release_skips_the_faculty_side_of_the_repo(tmp_path):
    # MAINTAINING.md is written into every materials repo by scaffold as "never released";
    # `.github` is the Release buttons and their bot-token wiring. Both are faculty-side, so
    # neither may ride along with "give me everything".
    ignore = deploy._copy_ignore(tmp_path)
    names = [".git", ".github", "MAINTAINING.md", "labs", "SYLLABUS.md"]
    assert ignore(str(tmp_path), names) == {".git", ".github", "MAINTAINING.md"}


def test_the_faculty_side_is_skipped_only_at_the_repo_root(tmp_path):
    # The exclusion is root-ANCHORED, not a basename glob: a faculty member's own
    # `labs/.github/` or `labs/MAINTAINING.md` is content, not release plumbing, and must
    # travel. `.git` is the exception - it is never copyable, at any depth.
    ignore = deploy._copy_ignore(tmp_path)
    names = [".git", ".github", "MAINTAINING.md", "01.md"]
    assert ignore(str(tmp_path / "labs"), names) == {".git"}


def test_naming_the_faculty_side_explicitly_still_releases_it(tmp_path):
    # The skip is what "everything" means, not a ban: releasing `.github` as a named
    # course_source_path is a subpath copy, which excludes nothing but `.git`.
    ignore = deploy._copy_ignore(None)
    assert ignore(str(tmp_path), [".git", ".github", "MAINTAINING.md"]) == {".git"}


# ----------------------------------------------------- the unedited-README guard
# The scaffold's README is addressed to FACULTY - "replace this placeholder", a section
# headed "delete this section before releasing", a link to MAINTAINING.md and the course
# org's Actions tab. Releasing it publishes all of that to students as their course
# overview, which is what happened in a live cohort, in three repos at once, silently.


def _scaffold_readme() -> str:
    """A README shaped like the one scaffold seeds. Built from the SHARED sentinel, so it
    cannot drift from the guard; that the real seeded file trips the guard is asserted
    end-to-end in test_scaffold.py."""
    return (
        "<!-- FACULTY & INSTRUCTORS: replace the content below -->\n\n"
        "# Course materials\n\n"
        "> **Replace this placeholder.** This file becomes the students' README.\n\n"
        f"## For faculty & instructors ({course.FACULTY_ONLY_HEADING})\n\n"
        "- see MAINTAINING.md\n"
    )


def test_the_scaffold_readme_is_recognised_as_unedited():
    assert deploy._is_withheld_stub("README.md", _scaffold_readme())


def test_a_real_readme_is_released():
    real = "# Foundations of Machine Learning\n\nWelcome. Slides go up Tuesdays.\n"
    assert not deploy._is_withheld_stub("README.md", real)


def test_a_readme_quoting_one_marker_is_still_released():
    # Both markers must be present. A real overview that happens to quote the stub - or a
    # half-edited one where the faculty section is already gone - is the faculty's writing,
    # and withholding it would be the guard overreaching.
    half = f"# Real overview\n\nWe kept a note: {course.FACULTY_ONLY_HEADING}\n"
    assert not deploy._is_withheld_stub("README.md", half)
    assert not deploy._is_withheld_stub(
        "README.md", "# Real\n\n> **Replace this placeholder.** (quoted)\n"
    )


def test_only_a_root_readme_is_guarded():
    # A README inside a section or session folder is the faculty's own writing about that
    # folder; the stub only ever exists at the repo root. Matching on the file NAME rather
    # than the whole path would have withheld those too.
    for nested in ("lectures/01_intro/README.md", "labs/README.md", "docs/README.md"):
        assert not deploy._is_withheld_stub(nested, _scaffold_readme())
    assert deploy._is_withheld_stub("README.md", _scaffold_readme())
    assert deploy._is_withheld_stub("/README.md", _scaffold_readme())


def test_an_unwritten_syllabus_stub_is_withheld_too():
    # It joined the guard the moment the site began PINNING the syllabus on the landing
    # page: an unwritten stub would otherwise be the most prominent link on the front page,
    # showing students "Optional - delete this file", empty tables and faculty instructions.
    from dsl_course import scaffold

    stub = scaffold._SYLLABUS_STUB.format(tag="f2026")
    assert deploy._is_withheld_stub("SYLLABUS.md", stub)
    # Written, so released.
    assert not deploy._is_withheld_stub(
        "SYLLABUS.md",
        "# Machine Learning\n\n## 1. General information\n\nReal content.\n",
    )
    # Root only, as for the README.
    assert not deploy._is_withheld_stub("docs/SYLLABUS.md", stub)


def test_the_excluded_root_files_are_named_from_one_place():
    # Re-spelling them per module is how an exclusion lapses when a file is renamed.
    assert course.SYLLABUS_SAMPLE_FILE in deploy.ROOT_RELEASE_EXCLUDED
    assert course.SYLLABUS_SESSIONS_FILE in deploy.ROOT_RELEASE_EXCLUDED


# ----------------------------- a bad symlink is one failed copy, not a dead cohort


def _one_file(src):
    (src / "sec").mkdir()
    (src / "sec" / "notes.md").write_text("real\n")


def _stub_deploy_many(monkeypatch, build_source, real_grants=False):
    """Drive deploy_many against local trees: `build_source(path)` fills each source clone,
    dest clones start empty, and nothing is committed.

    Returns `{dest repo: {relative path: "@link" or the file's text}}`, snapshotted at
    `git add` time - deploy_many's clones live in a TemporaryDirectory it deletes on the
    way out, so the state has to be captured while it is still there."""
    import os
    from pathlib import Path

    snapshots: dict[str, dict[str, str]] = {}

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            slug, target = args[2], Path(args[3])
            target.mkdir(parents=True, exist_ok=True)
            if slug.startswith("COURSE/"):
                build_source(target)
        return 0, ""

    def fake_git(*args, **kwargs):
        # `git diff --cached --quiet` == 0 means nothing staged, so deploy_many stops
        # before any commit or push: the copies are what this exercises.
        if len(args) > 1 and args[0] == "-C" and "add" in args:
            root = Path(args[1])
            snap: dict[str, str] = {}
            for dirpath, dirnames, filenames in os.walk(root):  # never follows links
                for name in list(dirnames) + filenames:
                    f = Path(dirpath) / name
                    rel = str(f.relative_to(root))
                    if f.is_symlink():
                        snap[rel] = "@link"
                    elif f.is_file():
                        snap[rel] = f.read_text()
            snapshots[root.name] = snap
        return 0, ""

    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    if not real_grants:
        monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
        monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "git", fake_git)
    return snapshots


def _deploy(path: str):
    from dsl_course.schedule import Deploy

    return Deploy(course_source_repo="materials", course_source_path=path)


def test_a_dangling_symlink_is_copied_as_a_link_not_followed(monkeypatch):
    # Followed, a link pointing at nothing raises shutil.Error - and this runs under the
    # hourly cron, so it aborted the whole cohort's release every hour.
    def build(src):
        (src / "sec").mkdir()
        (src / "sec" / "notes.md").write_text("real\n")
        (src / "sec" / "gone.md").symlink_to("nowhere.md")

    snaps = _stub_deploy_many(monkeypatch, build)
    errors, _changed = deploy.deploy_many(
        "COURSE", "COHORT", [_deploy("sec")], sync=False
    )
    assert errors == 0
    assert snaps["materials"]["sec/notes.md"] == "real\n"
    assert snaps["materials"]["sec/gone.md"] == "@link"


def test_a_directory_symlink_loop_does_not_recurse(monkeypatch):
    def build(src):
        (src / "sec").mkdir()
        (src / "sec" / "notes.md").write_text("real\n")
        (src / "sec" / "loop").symlink_to(src / "sec", target_is_directory=True)

    snaps = _stub_deploy_many(monkeypatch, build)
    errors, _changed = deploy.deploy_many(
        "COURSE", "COHORT", [_deploy("sec")], sync=False
    )
    assert errors == 0
    assert snaps["materials"]["sec/loop"] == "@link"


def test_one_unusable_path_is_one_counted_error_and_the_rest_still_ship(monkeypatch):
    import shutil

    def build(src):
        for name in ("bad", "good"):
            (src / name).mkdir()
            (src / name / "notes.md").write_text(name)

    snaps = _stub_deploy_many(monkeypatch, build)
    real_copytree = shutil.copytree

    def flaky(src, dst, **kwargs):
        if str(src).endswith("/bad"):
            raise shutil.Error("unreadable")
        return real_copytree(src, dst, **kwargs)

    monkeypatch.setattr(deploy.shutil, "copytree", flaky)
    errors, _changed = deploy.deploy_many(
        "COURSE", "COHORT", [_deploy("bad"), _deploy("good")], sync=False
    )
    assert errors == 1  # not an exception out of deploy_many
    assert snaps["materials"]["good/notes.md"] == "good"
    assert "bad/notes.md" not in snaps["materials"]
