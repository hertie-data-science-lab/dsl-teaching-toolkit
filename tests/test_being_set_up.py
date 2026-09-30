"""A semester the New semester wizard has listed, before Bootstrap semester has run: the
org-level dropdowns do not offer it, the course's profile page names it without a link, and
Bootstrap's preflight tells a semester how the bot comes to join it."""

from __future__ import annotations

from dsl_course import bootstrap_course, profile_readme, seed


def test_the_dropdowns_never_offer_a_semester_being_set_up(monkeypatch):
    offered = []
    monkeypatch.setattr(
        seed, "discover_semesters", lambda org: ["Course-f2026", "Course-s2027"]
    )
    monkeypatch.setattr(seed, "being_set_up", lambda org: org == "Course-s2027")
    monkeypatch.setattr(seed, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(seed, "discover_materials_repos", lambda org: [])
    monkeypatch.setattr(seed, "discover_assignments", lambda org: [])
    real = seed.render_provision

    def spy(semesters, assignments):
        offered.append(list(semesters))
        return real(semesters, assignments)

    monkeypatch.setattr(seed, "render_provision", spy)
    seed.github_workflow_files("Course", "release")
    assert offered == [["Course-f2026"]]


def test_the_course_page_names_a_semester_being_set_up_without_a_link():
    body = profile_readme.render_profile_readme(
        "Course",
        "Machine Learning",
        [],
        False,
        ["Course-f2026", "Course-s2027"],
        central_ref="release",
        setting_up=frozenset({"Course-s2027"}),
    )
    assert "- [Course-f2026](https://github.com/Course-f2026)" in body
    assert "- Course-s2027 _(being set up)_" in body
    assert "https://github.com/Course-s2027" not in body


def _preflight_text(monkeypatch, capsys, semester: bool) -> str:
    def gh(*args, **_kw):
        if args[1] == "user":
            return 0, "hertie-dsl-bot"
        if args[1].startswith("user/memberships"):
            return 0, "pending/admin"
        return 0, "Org"

    monkeypatch.setattr(bootstrap_course, "gh", gh)
    assert bootstrap_course.preflight("Org", semester=semester) is False
    return capsys.readouterr().out


def test_a_semester_preflight_says_how_the_bot_joins_a_semester(monkeypatch, capsys):
    out = _preflight_text(monkeypatch, capsys, semester=True)
    assert "list the org in the course's .github/semesters.yml" in out
    assert "orgs.yml" not in out


def test_a_course_preflight_says_the_dsl_team_registers_it(monkeypatch, capsys):
    out = _preflight_text(monkeypatch, capsys, semester=False)
    assert "the DSL team registers new courses; the bot then joins by itself" in out
