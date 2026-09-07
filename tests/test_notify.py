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
from conftest import source_fault

from dsl_course import mailer, notify, source_digest
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
        people: dict | None = PEOPLE,
        maintainer: str | None = "maint@x.edu",
        configured: bool = True,
    ) -> _Sent:
        monkeypatch.setattr(notify, "blame_logins", lambda *a, **k: blame or {})
        monkeypatch.setattr(notify, "last_committer", lambda *a, **k: committer)
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
    # `locate` returns None for a line it cannot find, and blaming line `None` would
    # address the mail to whoever happens to own line 1.
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
    assert "(+1 more)" in sent.one["subject"]
    assert sent.one["body"].count("<b>error line:</b>") == 2


@pytest.mark.parametrize(
    ("rung", "expected"),
    [
        (
            Severity.WARNING,
            "materials missing - releases Mon 7 Sep 15:00 Europe/Berlin",
        ),
        (Severity.URGENT, "(12h)"),
        (Severity.CRITICAL, "(6h)"),
        (Severity.MISSED, "released nothing - materials still missing"),
    ],
)
def test_the_subject_says_which_entry_and_how_long_is_left(wired, rung, expected):
    # The subject is what decides whether this gets opened today. It names the entry and
    # the deadline, with the zone - a time without a zone is a time about nothing.
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    _run([_fault()], rung, routing)
    assert sent.one["subject"].startswith(f"[{COHORT}] lecture_02 ")
    assert expected in sent.one["subject"]


def test_the_body_names_the_line_the_content_the_deadline_and_both_links(wired):
    sent = wired(blame={131: "JanG"}, committer=None)
    routing = notify.route(COHORT, COURSE, [_fault()], NOW)
    _run([_fault()], Severity.URGENT, routing)
    body = sent.one["body"]
    assert "<b>error line:</b>" in body
    assert "schedule.yml:131 - releases.lecture_02 -&gt; course_source_path" in body
    assert f"<b>error content:</b> {COURSE}/course-materials-f2026/" in body
    assert (
        "<b>fix by:</b>        release fires Mon 07 Sep 2026, 15:00 Europe/Berlin"
        in body
    )
    # The folder to push to comes FIRST, because staging the materials is the fix; the
    # schedule line second, because correcting the path is the other one.
    folder = "course-materials-f2026/tree/main/students/lectures"
    assert body.index(folder) < body.index("schedule.yml#L131")
    assert "<b>fix:</b> push the materials to that folder in" in body
    assert f'Record and history: <a href="{ISSUE}">' in body
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
    assert "<b>fix by:</b>" not in body
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
    assert "<b>fix by:</b>" not in sent.one["body"]


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
        "<b>fix:</b> create the assignment template repo named on schedule.yml:131"
        in body
    )
    assert "handout fires" in body
    # ...and it links the ORG the repo has to be created in. A `tree/main/` URL inside a
    # repo that does not exist is a 404 on the one line saying where to go.
    assert f'<a href="https://github.com/{COURSE}">' in body
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
