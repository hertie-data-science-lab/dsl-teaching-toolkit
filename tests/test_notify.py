"""notify -- who is told about a source fault, and what the mail says.

Two properties are load-bearing here and neither is visible from the code alone. The first
is ADDRESSING: git names the planner of the schedule line and the last committer of the
materials repo, and everything else - a TA's mail copying the instructors, the maintainer
joining at the last rung, the fallback when blame says nothing - hangs off that. The
second is the PUBLIC LOG RULE: every one of these runs in a public repo, so no line this
module prints may carry an address.

`mailer.send_bulk` is stubbed at the name `notify` imported, so what is asserted is the
batch this module handed the transport - recipients, cc, subject, body, HTML flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import yaml
from conftest import source_fault

from dsl_course import config_digest, mailer, notify, source_digest
from dsl_course.schedule import Severity, SourceFault

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=BERLIN)
COHORT = "Cohort-f2026"
COURSE = "Course-Org"
ISSUE = "https://github.com/Cohort-f2026/classroom-config/issues/7"
# The real `send_bulk`, kept before the `wired` fixture replaces it, so one test can put
# it back and exercise the log lines the transport itself prints.
_REAL_SEND_BULK = mailer.send_bulk
# `_fault`'s identity: the entry, the deploy's own path, the field. Two deploys under one
# entry are two faults, so the path is part of it.
_KEY = "releases.lecture_02[students/lectures/02_lecture].course_source_path"

# One instructor, one TA, one entry with no address at all - the three cases people.yml
# really contains.
PEOPLE = {
    "people": {
        "instructors": [
            {"github_handle": "JanG", "name": "Jan", "email": "jan@x.edu"},
            {"github_handle": "Nobody", "name": "No Address"},
        ],
        "teaching_assistants": [
            {"github_handle": "cpj97", "name": "Camilo", "email": "cam@x.edu"},
        ],
    }
}


def _fault(
    where: str = "releases.lecture_02",
    lineno: int | None = 131,
    repo: str = "course-materials-f2026",
    field: str = "course_source_path",
    path: str = "students/lectures/02_lecture",
    fires: timedelta = timedelta(hours=3),
) -> SourceFault:
    return source_fault(
        where,
        f"{COURSE}/{repo}/{path} does not exist",
        NOW + fires,
        field=field,
        lineno=lineno,
        repo=repo,
        path=path,
    )


def _digest(faults: list[SourceFault], rung: Severity) -> source_digest.DigestResult:
    """A digest result that owes a mail for every one of `faults` at `rung`."""
    by_key = {f.key: f for f in faults}
    return source_digest.DigestResult(
        issue_url=ISSUE,
        faults_by_key=by_key,
        mail=dict.fromkeys(by_key, rung),
    )


class _Sent:
    """Every MESSAGE handed to `send_bulk`, in order.

    One batch per tick now - the token is minted once - so what a test looks at is the
    messages inside it: who was on the To line as a group, who was copied, and the one
    body they all got."""

    def __init__(self):
        self.calls = 0
        self.batches: list[dict] = []

    def __call__(self, messages, dry_run=False, sample=None, html=False):
        self.calls += 1
        self.batches += [
            {
                "to": list(m.recipients),
                "subject": m.subject,
                "body": m.body,
                "cc": list(m.cc),
                "html": html,
            }
            for m in messages
        ]
        return [a for m in messages for a in m.recipients]

    @property
    def one(self) -> dict:
        (batch,) = self.batches
        return batch


@pytest.fixture
def wired(monkeypatch):
    """Stub everything that talks to GitHub or Graph, at the names `notify` imported.

    Blame and the repo's last committer are the two reads the addressing rests on, so a
    test that did not stub them would be asserting the fallback every time."""

    def _make(
        blame: dict[int, str] | None = None,
        committer: str | None = None,
        pushers: tuple[str, ...] | None = None,
        people: dict | None = PEOPLE,
        maintainer: str | None = "maint@x.edu",
        configured: bool = True,
    ) -> _Sent:
        monkeypatch.setattr(notify, "blame_logins", lambda *a, **k: blame or {})
        monkeypatch.setattr(notify, "last_committer", lambda *a, **k: committer)
        monkeypatch.setattr(
            notify, "path_committers", lambda *a, **k: tuple(pushers or ())
        )
        monkeypatch.setattr(
            notify.sync_faculty,
            "load_cohort_faculty",
            lambda *a, **k: (
                notify.sync_faculty.parse_faculty_from_meta(people)
                if people is not None
                else None
            ),
        )
        monkeypatch.setattr(notify.ghcli, "bot_login", lambda: "dsl-bot")
        monkeypatch.setattr(notify, "course_name_of", lambda org: "Course Name")
        monkeypatch.setattr(notify.mailer, "maintainer_address", lambda: maintainer)
        monkeypatch.setattr(
            notify.mailer,
            "graph_config_from_env",
            lambda: object() if configured else None,
        )
        sent = _Sent()
        monkeypatch.setattr(notify.mailer, "send_bulk", sent)
        return sent

    return _make


def _run(faults, rung, routing, dry_run=False):
    """The notifier, reporting how many addressees it FAILED to reach - which is what
    most of these tests are about. `_unsent` is for the two that look at which faults
    were held for the next tick."""
    return _unsent(faults, rung, routing, dry_run).addressees


def _unsent(faults, rung, routing, dry_run=False) -> notify.Unsent:
    return notify.notify_source_transitions(
        COHORT, COURSE, _digest(faults, rung), NOW, routing, dry_run=dry_run
    )


# ---------------------------------------------------------------- who is addressed


def test_the_planner_of_the_line_and_the_repos_committer_are_both_told(wired):
    # The two people who can act: one wrote the plan, the other is writing the materials.
    # Telling the whole teaching team about every entry is how a channel stops being read.
    wired(blame={131: "JanG"}, committer="cpj97")
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert routing.by_key[_KEY].to == (
        "jan@x.edu",
        "cam@x.edu",
    )
    assert routing.logins == ["JanG", "cpj97"]


def test_a_mail_to_a_ta_copies_the_instructors(wired):
    # A TA staging a lecture folder is doing it on somebody's behalf, and that somebody
    # needs to know the release is at risk without being the one asked to fix it.
    wired(blame={131: "cpj97"}, committer="cpj97")
    routed = notify.route(COHORT, COURSE, [_fault()], NOW).by_key[_KEY]
    assert routed.to == ("cam@x.edu",)
    assert routed.cc == ("jan@x.edu",)


def test_a_mail_to_an_instructor_copies_nobody(wired):
    wired(blame={131: "JanG"}, committer="JanG")
    routed = notify.route(COHORT, COURSE, [_fault()], NOW).by_key[_KEY]
    assert routed == (("jan@x.edu",), ())


def test_a_committer_who_is_not_teaching_staff_falls_back_to_the_team(wired):
    # A course admin, or somebody who has left. There is no address for them, so the
    # people who can act are whoever is teaching the cohort now.
    wired(blame={131: "a-stranger"}, committer=None)
    routed = notify.route(COHORT, COURSE, [_fault()], NOW).by_key[_KEY]
    assert set(routed.to) == {"jan@x.edu", "cam@x.edu"}
    # Nobody in particular to copy - everybody is already addressed.
    assert routed.cc == ()


def test_a_fault_with_no_line_number_falls_back_to_the_team(wired):
    # The parser records no line for an entry it could not place (`schedule._LineLoader`),
    # and blaming line `None` would address the mail to whoever happens to own line 1.
    wired(blame={131: "JanG"}, committer=None)
    routed = notify.route(COHORT, COURSE, [_fault(lineno=None)], NOW).by_key[_KEY]
    assert set(routed.to) == {"jan@x.edu", "cam@x.edu"}


def test_the_bot_is_never_the_person_to_tell(wired):
    # The bot writes handout_datetime back into schedule.yml and seeds every repo, so on a
    # file it has touched it is the blame answer for lines nobody at the school wrote.
    wired(blame={131: "dsl-bot"}, committer="dsl-bot")
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert set(routing.by_key[_KEY].to) == {
        "jan@x.edu",
        "cam@x.edu",
    }
    assert routing.logins == []


def test_the_same_person_named_twice_is_addressed_once(wired):
    wired(blame={131: "JanG"}, committer="jang")
    assert notify.route(COHORT, COURSE, [_fault()], NOW).by_key[_KEY].to == (
        "jan@x.edu",
    )


def test_blame_that_could_not_be_read_is_not_read_as_nobody(monkeypatch, capsys, wired):
    # A rate limit reported as "nobody wrote this" would address the mail to the wrong
    # people in silence. Degrading to the whole team is noisier and never wrong.
    wired(committer=None)

    def boom(*a, **k):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(notify, "blame_logins", boom)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert set(routing.by_key[_KEY].to) == {"jan@x.edu", "cam@x.edu"}
    assert "[skip] could not read who wrote schedule.yml" in capsys.readouterr().out


def test_an_addressee_with_no_email_is_counted_not_named(wired, capsys):
    wired(blame={131: "Nobody"}, committer=None)
    notify.route(COHORT, COURSE, [_fault()], NOW)
    out = capsys.readouterr().out
    assert "[skip] 1 addressee(s) without email" in out
    assert "Nobody" not in out  # the handle rides log_person, not the public log


def test_a_distant_fault_is_routed_to_nobody_and_costs_no_api_call(monkeypatch):
    # Below the digest's own rung nothing is said on either channel, so a quiet tick must
    # not spend a blame read on 22 entries nobody is going to hear about.
    def boom(*a, **k):
        raise AssertionError("no read should happen for an advisory-only plan")

    monkeypatch.setattr(notify, "blame_logins", boom)
    monkeypatch.setattr(notify.sync_faculty, "load_cohort_faculty", boom)
    assert notify.route(COHORT, COURSE, [_fault(fires=timedelta(days=40))], NOW) == (
        notify.Routing()
    )


# ------------------------------------------------------------------ what is sent


def test_the_maintainer_is_copied_only_at_the_last_two_rungs(wired):
    for rung in (Severity.WARNING, Severity.URGENT):
        sent = wired(blame={131: "JanG"}, committer=None)
        routing = notify.route(COHORT, COURSE, [_fault()], NOW)
        _run([_fault()], rung, routing)
        assert sent.one["cc"] == [], rung
    for rung in (Severity.CRITICAL, Severity.MISSED):
        sent = wired(blame={131: "JanG"}, committer=None)
        routing = notify.route(COHORT, COURSE, [_fault()], NOW)
        _run([_fault()], rung, routing)
        assert sent.one["cc"] == ["maint@x.edu"], rung


def test_two_committers_in_one_tick_get_a_mail_each(wired):
    # Grouped by recipient SET, not per fault: two entries the same person planned are one
    # conversation, and two people's entries are two.
    a = _fault("releases.lecture_02", lineno=131)
    b = _fault("releases.lecture_03", lineno=140)
    sent = wired(blame={131: "JanG", 140: "cpj97"}, committer=None)
    routing = notify.route(COHORT, COURSE, [a, b], NOW)
    _run([a, b], Severity.URGENT, routing)
    assert sorted(m["to"] for m in sent.batches) == [["cam@x.edu"], ["jan@x.edu"]]
    # ...in ONE batch, so the Graph token is minted once however many groups a tick has.
    assert sent.calls == 1


def test_a_whole_recipient_group_is_one_message(wired):
    # The fault mail is the same text for everybody on the line, and a copy per recipient
    # pays the rate limiter's send slot for each - so the group goes on one To line.
    sent = wired(blame={131: "a-stranger"}, committer=None)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    _run([_fault()], Severity.URGENT, routing)
    assert sorted(sent.one["to"]) == ["cam@x.edu", "jan@x.edu"]


def test_two_faults_from_one_person_share_a_single_mail(wired):
    a = _fault("releases.lecture_02", lineno=131)
    b = _fault("releases.lecture_03", lineno=140)
    sent = wired(blame={131: "JanG", 140: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [a, b], NOW)
    _run([a, b], Severity.URGENT, routing)
    assert sent.one["to"] == ["jan@x.edu"]
    assert "Missing materials: 2 releases, next fires" in sent.one["subject"]
    assert sent.one["body"].count("<b>error line:</b>") == 2


@pytest.mark.parametrize(
    ("rung", "fires", "expected"),
    [
        (Severity.WARNING, timedelta(hours=3), "fires Mon 7 Sep 15:00 - 24h left"),
        (Severity.URGENT, timedelta(hours=3), "fires Mon 7 Sep 15:00 - 12h left"),
        (Severity.CRITICAL, timedelta(hours=3), "fires Mon 7 Sep 15:00 - 6h left"),
        (
            Severity.MISSED,
            -timedelta(hours=1),
            "fired Mon 7 Sep 11:00 - nothing shipped",
        ),
    ],
)
def test_the_subject_says_which_entry_and_how_long_is_left(
    wired, rung, fires, expected
):
    # The subject is what decides whether this gets opened today. It names the course and
    # cohort a reader teaches, the entry, the deadline, and how much of it is left - the
    # zone lives in the body, where the same moment is spelled out in full.
    fault = _fault(fires=fires)
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [fault], NOW)
    _run([fault], rung, routing)
    assert sent.one["subject"] == (
        f"[Course Name f2026] Missing materials: lecture_02 {expected}"
    )


def test_the_body_names_the_line_the_content_the_deadline_and_both_links(wired):
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    _run([_fault()], Severity.URGENT, routing)
    body = sent.one["body"]
    assert body.startswith(
        "<p>This is an automated email sent on behalf of Course Name.</p>"
    )
    # The intro links the course org the materials belong in.
    assert f'<a href="https://github.com/{COURSE}">course org</a> yet.' in body
    # The line reference IS the deep link, then the entry and the field it names.
    line = f"https://github.com/{COHORT}/classroom-config/blob/main/schedule.yml#L131"
    assert (
        f'<b>error line:</b></td><td style="padding:0 0 0.25em 0">'
        f'<a href="{line}">schedule.yml:131</a> - releases.lecture_02 -&gt; '
        f"course_source_path</td>"
    ) in body
    # The content names the path as a path, not as a URL - the link is on the fix row.
    assert (
        "the specified path <code>students/lectures/02_lecture</code> does not exist."
        in body
    )
    assert "<b>fix by date:</b>" in body
    assert "release fires Mon 07 Sep 2026, 15:00 Europe/Berlin" in body
    # The fix names the repo to push to, linked to the folder's PARENT: the folder itself
    # is exactly what is not there yet.
    folder = f"https://github.com/{COURSE}/course-materials-f2026/tree/main/students/lectures"
    assert (
        f'<b>to fix:</b></td><td style="padding:0 0 0.25em 0">push the materials to '
        f'that folder in <a href="{folder}">{COURSE}/course-materials-f2026</a>, or '
        f"correct the path on the line above.</td>"
    ) in body
    assert (
        f'<b>GH issue record:</b></td><td style="padding:0 0 0.25em 0"><a href="{ISSUE}">'
        in body
    )
    assert "<pre>" not in body
    assert sent.one["html"] is True


def test_a_missed_release_is_told_in_the_past_tense(wired):
    # A release whose moment has actually gone by - which is what MISSED means, and what
    # the past tense below is read off (see `_block`).
    fired = _fault(fires=-timedelta(hours=1))
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [fired], NOW)
    _run([fired], Severity.MISSED, routing)
    body = sent.one["body"]
    assert "<b>fired:</b>" in body
    assert "<b>fix by date:</b>" not in body
    assert "the next 15-minute tick releases them" in body


def test_a_deadline_that_has_passed_is_past_tense_whatever_the_rung(wired):
    # A `.releaseignore`-withheld source is held at WARNING by its ceiling, and its date
    # goes by all the same. Reading "fired" off the rung told somebody to fix by a moment
    # that was yesterday.
    fault = source_fault(
        "releases.lecture_02",
        "the files exist but cm/.releaseignore keeps them back",
        NOW - timedelta(hours=2),
        kind=notify.FaultKind.WITHHELD,
        ceiling=Severity.WARNING,
        lineno=131,
        repo="cm",
        path="lectures/02",
    )
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [fault], NOW)
    _run([fault], Severity.WARNING, routing)
    assert "<b>fired:</b>" in sent.one["body"]
    assert "<b>fix by date:</b>" not in sent.one["body"]


def test_a_missing_assignment_template_says_to_create_the_repo(wired):
    # There is no path to push to yet, so "stage the materials" is not an instruction.
    fault = _fault(
        "assignments.assignment-2",
        repo="assignment-2-f2026",
        field="course_source_repo",
        path="",
    )
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [fault], NOW)
    _run([fault], Severity.URGENT, routing)
    body = sent.one["body"]
    assert (
        "the specified repo <code>assignment-2-f2026</code> does not exist or is empty."
        in body
    )
    assert "handout fires" in body
    # ...and the fix links the ORG the repo has to be created in. A `tree/main/` URL
    # inside a repo that does not exist is a 404 on the one line saying where to go.
    assert (
        f"create the assignment template repo named on schedule.yml:131 in "
        f'<a href="https://github.com/{COURSE}">{COURSE}</a> and push' in body
    )
    assert "tree/main" not in body


def test_a_release_whose_source_repo_is_missing_links_the_org_too(wired):
    # Same fault, from a `releases:` deploy rather than an assignment - so it carries a
    # path as well, and the path is not somewhere that can be linked either.
    fault = _fault(field="course_source_repo")
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [fault], NOW)
    _run([fault], Severity.URGENT, routing)
    assert f'<a href="https://github.com/{COURSE}">' in sent.one["body"]
    assert "tree/main" not in sent.one["body"]


def test_a_value_a_faculty_member_typed_cannot_break_out_of_the_html(wired):
    # `course_source_path: <tbc>` is a thing somebody writes, and unescaped it swallows
    # the rest of the mail - or worse, the fix line and the links.
    fault = _fault(where="releases.<script>x</script>", path="a<b")
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [fault], NOW)
    _run([fault], Severity.URGENT, routing)
    body = sent.one["body"]
    assert "<script>" not in body
    assert "releases.&lt;script&gt;x&lt;/script&gt;" in body


# -------------------------------------------------------------- when nothing is sent


def test_nothing_owed_sends_nothing(wired):
    sent = wired(blame={131: "JanG"}, committer=None)
    digest = source_digest.DigestResult(faults_by_key={}, mail={})
    assert (
        notify.notify_source_transitions(
            COHORT, COURSE, digest, NOW, notify.Routing(), dry_run=False
        )
        == notify.Unsent()
    )
    assert sent.batches == []


def test_a_cohort_with_no_addresses_at_all_says_so_once(wired, capsys):
    # ...and holds nothing: no address is a standing state, and a held crossing would be
    # recomputed - and commented on - every tick for the rest of the term.
    sent = wired(blame={}, committer=None, people={"people": {}})
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert _unsent([_fault()], Severity.URGENT, routing) == notify.Unsent()
    assert sent.batches == []
    assert "[skip] no notification address for" in capsys.readouterr().out


def test_an_org_with_no_mail_transport_says_so_once(wired, capsys):
    sent = wired(blame={131: "JanG"}, committer=None, configured=False)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert _unsent([_fault()], Severity.URGENT, routing) == notify.Unsent()
    assert sent.batches == []
    assert "[skip] mail not configured - issue @mention only" in capsys.readouterr().out


def test_a_dry_run_previews_and_sends_nothing(wired, capsys):
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert _unsent([_fault()], Severity.URGENT, routing, dry_run=True) == (
        notify.Unsent()
    )
    assert sent.batches == []
    assert "[dry-run] would mail 1 recipient(s)" in capsys.readouterr().out


def test_a_transport_that_raised_is_counted_not_propagated(monkeypatch, wired, capsys):
    # This runs inside a release cron. A credential Graph refused is not worth a release.
    wired(blame={131: "JanG"}, committer=None)

    def boom(*a, **k):
        raise RuntimeError("Graph said no")

    monkeypatch.setattr(notify.mailer, "send_bulk", boom)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    # Nothing went out, so everything this tick owed is handed back to be owed again.
    assert _unsent([_fault()], Severity.URGENT, routing) == notify.Unsent(1, (_KEY,))
    assert "could not mail" in capsys.readouterr().err


def test_a_message_that_did_not_land_is_reported_and_handed_back(
    monkeypatch, wired, capsys
):
    # The digest has already recorded the new rung by now, so a mail that failed here was
    # owed once, failed once and would never be owed again. The keys go back.
    wired(blame={131: "JanG"}, committer=None)
    monkeypatch.setattr(notify.mailer, "send_bulk", lambda *a, **k: [])
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert _unsent([_fault()], Severity.URGENT, routing) == notify.Unsent(1, (_KEY,))
    err = capsys.readouterr().err
    assert "were not reached - held for the next tick" in err
    assert "[err]" in err


def test_one_group_failing_does_not_hold_the_group_that_landed(monkeypatch, wired):
    # A group is one Graph POST for one message, so its recipients are all in or all out -
    # and the other group's fault has been delivered and must not be said twice.
    a = _fault("releases.lecture_02", lineno=131)
    b = _fault("releases.lecture_03", lineno=140)
    wired(blame={131: "JanG", 140: "cpj97"}, committer=None)
    monkeypatch.setattr(notify.mailer, "send_bulk", lambda ms, **k: ["jan@x.edu"])
    routing = notify.route(COHORT, COURSE, [a, b], NOW)
    out = _unsent([a, b], Severity.URGENT, routing)
    assert out.keys == (b.key,)
    assert out.addressees == 1


# ------------------------------------------------------------- the public log rule


def test_no_line_this_module_prints_carries_an_address(wired, capsys):
    # Every faculty workflow runs in a PUBLIC repo, so its Actions log is world-readable.
    # A count is what a reader needs; an address is a roster entry.
    sent = wired(blame={131: "cpj97"}, committer=None)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    _run([_fault()], Severity.CRITICAL, routing)
    printed = capsys.readouterr()
    for line in (printed.out + printed.err).splitlines():
        assert "@x.edu" not in line, line
    assert "mailed 1 recipient(s) in 1 message(s)" in printed.out
    # ...and the mail itself did go to the TA, with the instructors and the maintainer on
    # the Cc line.
    assert sent.one["to"] == ["cam@x.edu"]
    assert sent.one["cc"] == ["jan@x.edu", "maint@x.edu"]


def test_a_people_yml_that_will_not_parse_never_prints_the_line_it_broke_on(
    wired, monkeypatch, capsys
):
    # PyYAML renders the offending SOURCE LINE into `str(exc)`, and the line that breaks a
    # people.yml is as often as not the one carrying somebody's address. The parser's own
    # complaint and the line NUMBER say everything a reader needs (`read_error`).
    wired(blame={131: "cpj97"}, committer=None)

    def boom(*a, **k):
        yaml.safe_load("people:\n  instructors:\n  - email: jan@x.edu: typo\n")

    monkeypatch.setattr(notify.sync_faculty, "load_cohort_faculty", boom)
    notify.route(COHORT, COURSE, [_fault()], NOW)
    err = capsys.readouterr().err
    assert "jan@x.edu" not in err
    assert f"could not read {COHORT}'s people.yml" in err
    assert "ScannerError: mapping values are not allowed here (line 3)" in err


def test_the_transport_itself_names_nobody_in_the_log(monkeypatch, capsys, wired):
    # The test above stubs `send_bulk`, which is where the notifier's own counts are
    # printed - so the rule is pinned again through the real batch loop, with nothing
    # removed but the Graph POST. That loop used to log `sent -> j***@pm.me` per message,
    # and its retry and failure lines named the group too.
    wired(blame={131: "cpj97"}, committer=None)
    posted = []

    def _post(cfg, token, msg, html=False):
        posted.append(msg)
        return True

    monkeypatch.setattr(notify.mailer, "send_bulk", _REAL_SEND_BULK)
    monkeypatch.setattr(notify.mailer, "_graph_token", lambda cfg: "tok")
    monkeypatch.setattr(notify.mailer, "_graph_send_one", _post)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    assert _unsent([_fault()], Severity.CRITICAL, routing) == notify.Unsent()
    printed = capsys.readouterr()
    for line in (printed.out + printed.err).splitlines():
        assert "@x.edu" not in line and "***@" not in line, line
    assert "sent -> 1 recipient(s)" in printed.out
    # ...and the send itself really was addressed: the log is what is quiet, not the mail.
    assert [(m.recipients, m.cc) for m in posted] == [
        (("cam@x.edu",), ("jan@x.edu", "maint@x.edu"))
    ]


# ------------------------------------------------------------------ a failed run


def test_the_run_failure_mail_carries_the_url_and_the_tail(monkeypatch):
    sent = _Sent()
    monkeypatch.setattr(notify.mailer, "send_bulk", sent)
    monkeypatch.setattr(notify.mailer, "maintainer_address", lambda: "maint@x.edu")
    assert (
        notify.notify_run_failed(COURSE, "Scheduled release", "https://run/1", "x\ny")
        == 0
    )
    assert sent.one["to"] == ["maint@x.edu"]
    assert sent.one["subject"] == f"[{COURSE}] Scheduled release is failing"
    assert "https://run/1" in sent.one["body"]
    assert sent.one["body"].endswith("Last lines of the failed step:\n\nx\ny\n")
    # Plain text: the tail is preformatted and marking it up would only mangle it.
    assert sent.one["html"] is False


def test_the_log_tail_is_capped_in_lines_and_in_bytes():
    # One line of a stack-trace dump can be very long indeed, and a mail is not a log file.
    assert notify._tail("\n".join(str(i) for i in range(100))).startswith("70\n")
    assert len(notify._tail("x" * 10_000).encode()) <= notify._TAIL_BYTES


def test_with_no_maintainer_address_the_failure_issue_is_the_only_channel(
    monkeypatch, capsys
):
    sent = _Sent()
    monkeypatch.setattr(notify.mailer, "send_bulk", sent)
    monkeypatch.setattr(notify.mailer, "maintainer_address", lambda: None)
    assert notify.notify_run_failed(COURSE, "Refresh actions", "https://run/1", "") == 0
    assert sent.batches == []
    assert "[skip] mail not configured" in capsys.readouterr().out


# --------------------------------------------------- a file the toolkit cannot read
#
# The other half of the notifier: an immediate fault, which has no deadline to escalate
# towards. Everything the source mail gets from the rung this one has to get from the
# clock the digest keeps - and the file it is about is full of personal data, so what the
# mail may say about a row is the row number and the column.

CSV_ISSUE = "https://github.com/Cohort-f2026/classroom-config/issues/12"


def _row_fault(lineno: int = 4, field: str = "role") -> notify.ConfigFault:
    return notify.ConfigFault(
        f"row {lineno}",
        "unrecognised role - the row is treated as enrolled",
        file="students.csv",
        field=field,
        lineno=lineno,
        fix_text=f"fix row {lineno} of students.csv",
    )


def _people_fault() -> notify.ConfigFault:
    return notify.ConfigFault(
        "people.instructors[1]",
        "no usable `email:` - access is still granted, but no notification reaches "
        "this person",
        file="people.yml",
        field="email",
        lineno=6,
    )


def _config_digest_result(faults, reminder=None) -> source_digest.DigestResult:
    by_key = {f.key: f for f in faults}
    return source_digest.DigestResult(
        issue_url=CSV_ISSUE,
        faults_by_key=by_key,
        mail=dict.fromkeys(by_key, Severity.WARNING),
        reminder=reminder,
    )


def _mail_config(faults, routing, reminder=None, spec=None, dry_run=False):
    return notify.notify_config_faults(
        spec or config_digest.ROSTER,
        COHORT,
        COURSE,
        _config_digest_result(faults, reminder),
        NOW,
        routing,
        dry_run=dry_run,
    )


def test_a_csv_fault_is_addressed_to_whoever_pushed_the_file(wired):
    # NOT blame: the bot writes `enrol_code` and `code_sent_at` back into rows faculty
    # typed, so half the roster blames to an account that cannot fix anything.
    wired(blame={4: "dsl-bot"}, pushers=("dsl-bot", "JanG"))
    routing = notify.route(COHORT, COURSE, [_row_fault()], NOW)
    (routed,) = routing.by_key.values()
    assert routed.to == ("jan@x.edu",)
    assert routing.logins == ["JanG"]


def test_a_yaml_fault_is_addressed_by_blame_of_its_own_file(wired):
    wired(blame={6: "cpj97"})
    routing = notify.route(COHORT, COURSE, [_people_fault()], NOW)
    (routed,) = routing.by_key.values()
    assert routed.to == ("cam@x.edu",)
    assert routed.cc == ("jan@x.edu",)  # a TA's mail copies the instructors


def test_git_naming_nobody_falls_back_to_the_whole_teaching_team(wired):
    wired(pushers=("dsl-bot",))
    routing = notify.route(COHORT, COURSE, [_row_fault()], NOW)
    (routed,) = routing.by_key.values()
    assert routed.to == ("jan@x.edu", "cam@x.edu")


def test_the_mail_says_what_the_file_is_costing_the_cohort(wired):
    sent = wired(pushers=("JanG",))
    routing = notify.route(COHORT, COURSE, [_row_fault()], NOW)
    _mail_config([_row_fault()], routing)
    assert sent.one["subject"] == (
        "[Course Name f2026] students.csv has 1 entry the toolkit cannot use"
    )
    assert "A recent edit to <code>students.csv</code> left 1 entry" in sent.one["body"]
    assert "the whole roster is skipped" in sent.one["body"]
    assert sent.one["html"] is True


def test_a_dated_fault_is_not_introduced_as_a_recent_edit(wired):
    # An assignment's grading_config.yml is the one file here whose faults carry a moment:
    # they are held until the grading pass that reads them is close, so the letter goes out
    # months after the line was written and "a recent edit" names the wrong week - under a
    # table whose own row already says when it bites.
    dated = notify.ConfigFault(
        "assignments.a3",
        "`submit_via: emial` is not one of github/email - using github",
        fires=NOW + timedelta(hours=6),
        file="grading_config.yml",
        field="submit_via",
        lineno=4,
        in_repo="assignment-3",
        in_org=COURSE,
        ref="solution",
    )
    sent = wired(blame={4: "JanG"})
    routing = notify.route(COHORT, COURSE, [dated], NOW)
    _mail_config([dated], routing, spec=config_digest.GRADING_CONFIG)
    body = sent.one["body"]
    assert "A recent edit" not in body
    assert "<code>grading_config.yml</code> has 1 entry the toolkit cannot use" in body
    assert "grading fires" in body  # the row that does say when


def test_two_faults_are_one_message_and_the_subject_counts_them(wired):
    sent = wired(pushers=("JanG",))
    faults = [_row_fault(4), _row_fault(9, field="github_handle")]
    routing = notify.route(COHORT, COURSE, faults, NOW)
    _mail_config(faults, routing)
    assert "2 entries the toolkit cannot use" in sent.one["subject"]


def test_the_mail_carries_the_row_the_column_and_the_fix_and_nothing_else(wired):
    sent = wired(pushers=("JanG",))
    routing = notify.route(COHORT, COURSE, [_row_fault()], NOW)
    _mail_config([_row_fault()], routing)
    body = sent.one["body"]
    assert "students.csv:4" in body and "row 4 -&gt; role" in body
    assert "fix row 4 of students.csv" in body
    assert CSV_ISSUE in body
    # No deadline row: an unreadable line does not happen at a time.
    assert "fix by date" not in body and "no date (tbc)" not in body


def test_the_first_mail_does_not_copy_the_maintainer(wired):
    sent = wired(pushers=("JanG",))
    routing = notify.route(COHORT, COURSE, [_row_fault()], NOW)
    _mail_config([_row_fault()], routing)
    assert sent.one["cc"] == []


def test_a_reminder_changes_the_first_line_and_copies_the_maintainer(wired):
    sent = wired(pushers=("JanG",))
    routing = notify.route(COHORT, COURSE, [_row_fault()], NOW)
    _mail_config([_row_fault()], routing, reminder="2 days")
    assert "Still unfixed after 2 days:" in sent.one["body"]
    assert sent.one["cc"] == ["maint@x.edu"]


def test_a_people_fault_names_its_own_file(wired):
    sent = wired(blame={6: "JanG"})
    routing = notify.route(COHORT, COURSE, [_people_fault()], NOW)
    _mail_config([_people_fault()], routing, spec=config_digest.PEOPLE)
    assert "people.yml has 1 entry the toolkit cannot use" in sent.one["subject"]
    assert "this person has no access and is not notified" in sent.one["body"]
    assert "people.yml#L6" in sent.one["body"]


def _sheet_fault() -> notify.ConfigFault:
    """A mark a grader typed that is not a number - the sheet's own shape of fault: the
    line and never the unit, because the unit key is a student's handle."""
    return notify.ConfigFault(
        "a3 line 5",
        "a mark on this line is not a number, so nothing for this unit is sent",
        file="grading_sheets/a3.yml",
        field="score_individual",
        lineno=5,
        fix_text="correct the mark on line 5 - a mark must be a number, or blank",
    )


def test_a_grading_sheet_fault_links_the_sheet_it_is_in_not_the_folder(wired):
    # ONE issue and one letter for every sheet in the cohort, and every line in it links
    # the sheet it is about: the digest names the folder, the fault names the file.
    sent = wired(blame={5: "JanG"})
    routing = notify.route(COHORT, COURSE, [_sheet_fault()], NOW)
    _mail_config([_sheet_fault()], routing, spec=config_digest.GRADING_SHEETS)
    assert sent.one["subject"] == (
        "[Course Name f2026] grading_sheets/ has 1 entry the toolkit cannot use"
    )
    assert "that sheet is not refreshed, and nothing on it is sent" in sent.one["body"]
    assert "classroom-config/blob/main/grading_sheets/a3.yml#L5" in sent.one["body"]
    assert "a3 line 5 -&gt; score_individual" in sent.one["body"]


def test_a_grading_sheet_mail_never_names_the_student_it_is_about(wired):
    sent = wired(blame={5: "JanG"})
    routing = notify.route(COHORT, COURSE, [_sheet_fault()], NOW)
    _mail_config([_sheet_fault()], routing, spec=config_digest.GRADING_SHEETS)
    assert "ada-l" not in sent.one["body"] and "ada-l" not in sent.one["subject"]


def _grading_config_fault() -> notify.ConfigFault:
    """A value in an assignment's definition that will not grade as written - the one
    hand-edited file whose faults have a MOMENT, and the one that is not in the cohort org
    at all."""
    return notify.ConfigFault(
        "assignments.a3",
        "`submit_via: emial` is not one of github/external - using `github`",
        fires=NOW + timedelta(hours=8),
        field="submit_via",
        lineno=3,
        file="grading_config.yml",
        in_repo="assignment-3-f2026",
        in_org=COURSE,
        ref="solution",
        fix_text="correct the value on the line above (allowed: github/external)",
    )


def test_a_grading_config_fault_asks_the_template_who_wrote_the_line(
    wired, monkeypatch
):
    # Blaming the cohort's classroom-config for a file in the course org would name
    # nobody, and the cohort would be told by its instructors team instead of by the
    # person who typed it.
    wired()
    asked: list = []
    monkeypatch.setattr(
        notify,
        "blame_logins",
        lambda org, repo, path, ref: (
            asked.append((org, repo, path, ref)) or {3: "JanG"}
        ),
    )
    routing = notify.route(COHORT, COURSE, [_grading_config_fault()], NOW)
    assert asked == [
        (COURSE, "assignment-3-f2026", "grading_config.yml", "refs/heads/solution")
    ]
    (routed,) = routing.by_key.values()
    assert routed.to == ("jan@x.edu",)


def test_a_grading_config_mail_says_when_the_value_is_used(wired):
    sent = wired(blame={3: "JanG"})
    fault = _grading_config_fault()
    routing = notify.route(COHORT, COURSE, [fault], NOW)
    _mail_config([fault], routing, spec=config_digest.GRADING_CONFIG)
    body = sent.one["body"]
    assert "grading uses the toolkit&#x27;s default for that value" in body
    # A deadline row, because this fault HAS one, and it names the grading moment: the
    # handout this entry describes went out weeks ago.
    assert "fix by date" in body and "grading fires" in body
    assert "assignment-3-f2026/blob/solution/grading_config.yml#L3" in body


def test_no_mail_transport_says_so_once_and_never_raises(wired, capsys):
    wired(pushers=("JanG",), configured=False)
    routing = notify.route(COHORT, COURSE, [_row_fault()], NOW)
    assert _mail_config([_row_fault()], routing).addressees == 0
    assert "mail not configured" in capsys.readouterr().out


# --------------------------------------------- a fault in the COURSE org's own config
#
# The course org's dsl-course.yml and cohort registry decide whether the course is synced
# at all. Their addressees are the course ADMINS, out of an org secret - never out of the
# public file half these faults are in - so there is no handle to address one of them by,
# and git's answer goes to the digest's @mention instead of to the To line.

COURSE_ISSUE = "https://github.com/Course-Org/.github/issues/3"
ADMINS = ("lonny@x.edu", "luis@x.edu")


def _course_fault(field: str = "central_ref", lineno: int | None = 8):
    return notify.ConfigFault(
        "dsl-course.yml",
        "`central_ref:` is not `main`, `release` or a full 40-character commit SHA",
        file="dsl-course.yml",
        field=field,
        in_repo=".github",
        lineno=lineno,
    )


def _course_digest(faults, reminder=None) -> source_digest.DigestResult:
    by_key = {f.key: f for f in faults}
    return source_digest.DigestResult(
        issue_url=COURSE_ISSUE,
        faults_by_key=by_key,
        mail=dict.fromkeys(by_key, Severity.WARNING),
        reminder=reminder,
    )


def _mail_course(faults, routing, reminder=None):
    return notify.notify_config_faults(
        config_digest.COURSE,
        COURSE,
        COURSE,
        _course_digest(faults, reminder),
        NOW,
        routing,
        dry_run=False,
    )


@pytest.fixture
def admins(monkeypatch):
    """The `DSL_COURSE_ADMIN_EMAILS` org secret, as a course org's workflows read it."""

    def _set(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv(mailer.COURSE_ADMIN_ENV, raising=False)
        else:
            monkeypatch.setenv(mailer.COURSE_ADMIN_ENV, value)

    return _set


def test_a_course_fault_is_addressed_to_the_admins_and_mentions_the_committer(
    wired, admins
):
    admins(", ".join(ADMINS))
    wired(blame={8: "JanG"})
    routing = notify.route_course(COURSE, [_course_fault()], NOW)
    (routed,) = routing.by_key.values()
    # The admins act on it; the person git names is @mentioned on the issue, because the
    # secret is a flat address list with no handle to match them against.
    assert routed.to == ADMINS and routed.cc == ()
    assert routing.logins == ["JanG"]


def test_a_fault_about_the_whole_file_falls_back_to_whoever_pushed_it(wired, admins):
    # Missing, unparseable, the wrong shape: no line to blame, and precisely the faults
    # somebody has just pushed.
    admins(ADMINS[0])
    wired(blame={8: "JanG"}, pushers=("dsl-bot", "cpj97"))
    routing = notify.route_course(COURSE, [_course_fault(lineno=None)], NOW)
    assert routing.logins == ["cpj97"]


def test_a_line_the_bot_wrote_falls_through_to_whoever_pushed_it(wired, admins):
    # The bot writes `central_ref:` into every dsl-course.yml, so blame is its login for
    # lines nobody at the school has ever typed. @mentioning it on the issue reaches
    # nobody and hides the person who actually pushed the file.
    admins(ADMINS[0])
    wired(blame={8: "dsl-bot"}, pushers=("dsl-bot", "cpj97"))
    routing = notify.route_course(COURSE, [_course_fault()], NOW)
    assert routing.logins == ["cpj97"]


def test_the_maintainer_is_copied_on_the_very_first_course_mail(wired, admins):
    # Not at 48 hours as a cohort's file is: the course admins in the To line are the same
    # small group who may have written the line, so there is nobody else to notice.
    admins(",".join(ADMINS))
    sent = wired(blame={8: "JanG"})
    routing = notify.route_course(COURSE, [_course_fault()], NOW)
    _mail_course([_course_fault()], routing)
    assert sent.one["cc"] == ["maint@x.edu"]
    assert sent.one["to"] == list(ADMINS)


def test_the_course_subject_names_the_course_and_no_cohort(wired, admins):
    admins(ADMINS[0])
    sent = wired(blame={8: "JanG"})
    routing = notify.route_course(COURSE, [_course_fault()], NOW)
    _mail_course([_course_fault()], routing)
    # No term tag: this is the course org's own file, not a cohort's.
    assert sent.one["subject"] == (
        "[Course Name] dsl-course.yml has 1 entry the toolkit cannot use"
    )
    assert "the sync skips this course" in sent.one["body"]
    assert "dsl-course.yml#L8" in sent.one["body"]


def test_a_course_org_with_no_admin_secret_says_so_without_naming_anyone(
    wired, admins, capsys
):
    # Appendix H's degraded mode: a count and the variable's name, in a PUBLIC repo's log.
    admins(None)
    sent = wired(blame={8: "JanG"})
    routing = notify.route_course(COURSE, [_course_fault()], NOW, ["jan-g", "lonny"])
    assert _mail_course([_course_fault()], routing).addressees == 0
    out = capsys.readouterr().out
    assert "[skip] 2 addressee(s) without email" in out
    assert mailer.COURSE_ADMIN_ENV in out
    assert "the digest @mention is the only channel" in out
    assert sent.calls == 0


def test_a_stray_comma_in_the_secret_costs_nobody_a_mail(admins):
    admins(" lonny@x.edu ,, not-an-address, luis@x.edu")
    assert mailer.course_admin_addresses() == ("lonny@x.edu", "luis@x.edu")


def test_no_course_admin_address_ever_reaches_the_log(wired, admins, capsys):
    admins(",".join(ADMINS))
    wired(blame={8: "JanG"})
    routing = notify.route_course(COURSE, [_course_fault()], NOW)
    _mail_course([_course_fault()], routing)
    out = capsys.readouterr().out
    assert all(address not in out for address in ADMINS)


def test_a_dsl_course_yml_nobody_can_parse_still_sends_its_mail(
    wired, admins, monkeypatch
):
    # The sender line asks the course org for its display name, which reads the very file
    # this mail is about. The one fault that most needs an email must not be the one that
    # sends none - it falls back to the org slug and goes out.
    admins(ADMINS[0])
    sent = wired(blame={8: "JanG"})

    def unparseable(org):
        raise yaml.YAMLError("bad")

    monkeypatch.setattr(notify, "course_name_of", unparseable)
    routing = notify.route_course(COURSE, [_course_fault()], NOW)
    assert _mail_course([_course_fault()], routing).addressees == 0
    assert sent.one["subject"].startswith(f"[{COURSE}] dsl-course.yml has 1 entry")


# ------------------------------------------- an edit the site sync rebuilt over


SITE = "cohort-f2026.github.io"
SITE_ISSUE = "https://github.com/Cohort-f2026/cohort-f2026.github.io/issues/3"
SHA = "a1b2c3d4e5f67890"


def _overwritten(by_login, dry_run=False) -> notify.Unsent:
    return notify.notify_overwritten_edits(
        COHORT, SITE, COURSE, by_login, SITE_ISSUE, NOW, dry_run=dry_run
    )


def test_the_person_whose_edit_was_rebuilt_over_is_the_one_told(wired):
    # The committer, by the login GitHub gave the commit - not blame, and not the team:
    # the file was rewritten whole, so there is no line in it that is anybody's edit.
    sent = wired()
    _overwritten({"JanG": [("_data/people.yml", SHA)]})
    assert sent.one["to"] == ["jan@x.edu"]
    assert sent.one["cc"] == []  # never the maintainer: this is a habit, not an outage
    assert sent.one["subject"] == (
        "[Course Name f2026] Your edit to _data/people.yml was overwritten by the "
        "site sync"
    )
    assert sent.one["html"] is True


def test_the_mail_says_what_was_lost_where_it_is_and_what_to_do(wired):
    sent = wired()
    _overwritten({"JanG": [("_data/people.yml", SHA)]})
    body = sent.one["body"]
    assert (
        "Your edit to <code>_data/people.yml</code> was overwritten by the sync" in body
    )
    assert "generated files are rebuilt every run" in body
    # The commit is the only place the change still exists, so it is linked.
    assert f"https://github.com/{COHORT}/{SITE}/commit/{SHA}" in body
    assert "a1b2c3d" in body
    assert "move the change to the file the docs name as yours" in body
    assert SITE_ISSUE in body
    # No deadline and no line: neither exists for a file that was rebuilt whole.
    assert "fix by date" not in body and ":None" not in body


def test_a_ta_who_lost_an_edit_has_the_instructors_copied(wired):
    sent = wired()
    _overwritten({"cpj97": [("_events/final.md", SHA)]})
    assert sent.one["to"] == ["cam@x.edu"]
    assert sent.one["cc"] == ["jan@x.edu"]


def test_an_author_github_cannot_name_reaches_the_whole_team(wired):
    # The incident itself: a git email linked to no account is un-@mentionable, so the
    # issue reached nobody. The empty key is that case, and the team is the fallback.
    sent = wired()
    _overwritten({"": [("_data/people.yml", SHA)]})
    assert sent.one["to"] == ["jan@x.edu", "cam@x.edu"]


def test_two_files_from_one_person_are_one_letter_that_counts_them(wired):
    sent = wired()
    _overwritten(
        {"JanG": [("_data/people.yml", SHA), ("_events/final.md", "ffffffffffff")]}
    )
    assert sent.one["subject"] == (
        "[Course Name f2026] 2 of your edits were overwritten by the site sync"
    )
    assert "2 files you edited were overwritten" in sent.one["body"]
    assert sent.one["body"].count("error line:") == 2


def test_two_people_who_lost_edits_get_a_letter_each(wired):
    sent = wired()
    _overwritten(
        {"JanG": [("_data/people.yml", SHA)], "cpj97": [("_events/final.md", SHA)]}
    )
    assert [b["to"] for b in sent.batches] == [["jan@x.edu"], ["cam@x.edu"]]
    assert sent.calls == 1  # one batch, one Graph token


def test_a_site_org_with_nobody_to_address_sends_nothing(wired, capsys):
    # The public COURSE site: the org declares no people.yml at all, so the issue's cc is
    # the only channel there is. Not an error, and not a raise inside a site sync.
    sent = wired(people=None)
    assert _overwritten({"JanG": [("_data/people.yml", SHA)]}) == notify.Unsent()
    assert sent.batches == []
    assert "no notification address" in capsys.readouterr().out


def test_nothing_overwritten_asks_github_for_nothing(wired):
    sent = wired()
    assert _overwritten({}) == notify.Unsent()
    assert sent.batches == []


def test_an_unreadable_people_file_never_raises_into_the_sync(wired, monkeypatch):
    # This runs after the site is pushed. Whatever went wrong with the notification, the
    # sync's exit code is not the place to say so.
    wired()

    def boom(*a, **k):
        raise RuntimeError("HTTP 502")

    monkeypatch.setattr(notify.sync_faculty, "load_cohort_faculty", boom)
    assert _overwritten({"JanG": [("_data/people.yml", SHA)]}) == notify.Unsent()


def test_no_address_reaches_the_public_run_log(wired, capsys):
    sent = wired()
    _overwritten({"JanG": [("_data/people.yml", SHA)]})
    printed = capsys.readouterr()
    assert "jan@x.edu" not in printed.out + printed.err
    assert sent.one["to"] == ["jan@x.edu"]
