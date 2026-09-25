"""dsl_course.schedule pure core - semester-config/schedule.yml is the single home for a
semester's release plan (releases), due dates (assignments), and display-only calendar rows
(events); a wrong parse here silently mis-times a release or mis-pins a grading deadline,
so it's the bit that must be right. Times are timezone-aware (naive -> Europe/Berlin by
default).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from conftest import source_fault

from dsl_course import course, gh_contents, policy, schedule, settings
from dsl_course import faults as faults_module
from dsl_course.schedule import (
    AssignmentEntry,
    Deploy,
    Event,
    Release,
    Schedule,
    Severity,
    _coerce_date,
    _coerce_datetime,
    parse,
)

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.mark.parametrize(
    "value,expected",
    [
        (date(2026, 9, 7), date(2026, 9, 7)),
        (datetime(2026, 9, 7, 12, 0), date(2026, 9, 7)),
        ("2026-09-07", date(2026, 9, 7)),
        ("not-a-date", None),
        (12345, None),
    ],
)
def test_coerce_date(value, expected):
    assert _coerce_date(value) == expected


def test_coerce_datetime_bare_date_start_or_end_of_day():
    # A release date opens at the start of the day; a due date closes at the end.
    start = _coerce_datetime(date(2026, 9, 15), BERLIN)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    end = _coerce_datetime(date(2026, 10, 13), BERLIN, end_of_day=True)
    assert (end.hour, end.minute, end.second) == (23, 59, 59)


def test_coerce_datetime_naive_gets_the_semester_tz_and_an_offset_is_converted_to_it():
    naive = _coerce_datetime("2026-09-15T14:00", BERLIN)
    assert naive.tzinfo is not None
    assert naive.utcoffset() == BERLIN.utcoffset(naive.replace(tzinfo=None))
    # An explicit offset names an INSTANT; it is honoured as that instant, but stored in
    # the semester's own clock - 14:00 UTC is 16:00 in Berlin in September. Every consumer
    # then reads a semester wall-clock time without re-deriving the zone (the site used to
    # convert at print time, and anything that forgot printed the wrong hour).
    aware = _coerce_datetime("2026-09-15T14:00+00:00", BERLIN)
    assert aware == datetime(2026, 9, 15, 14, 0, tzinfo=ZoneInfo("UTC"))  # same instant
    assert aware.tzinfo is BERLIN and (aware.hour, aware.minute) == (16, 0)


def _instance(text: str) -> settings.Instance:
    return settings.parse_instance(text)


def _parse_moved(meta: dict) -> schedule.Schedule:
    """`meta` parsed with each entry's `semester_dest_repo` where it lives now: the
    semester's assignments.yml."""
    blocks = {
        slug: {"semester_dest_repo": entry.pop("semester_dest_repo")}
        for slug, entry in meta["assignments"].items()
        if "semester_dest_repo" in entry
    }
    return parse(meta, _instance(yaml.safe_dump({"assignments": blocks})))


def test_parse_full_schedule():
    meta = {
        "timezone": "Europe/Berlin",
        "semester_start": "2026-09-07",
        "semester_end": "2026-12-18",
        "releases": {
            "session_2": {
                "event_datetime": "2026-09-15T14:00",
                "deploy": [
                    {
                        "course_source_repo": "cm-f2026",
                        "course_source_path": "lectures/02_intro",
                        "semester_dest_repo": "materials",
                        "semester_dest_path": "lectures/02_intro",
                    }
                ],
            },
            "a1-handout": {"event_datetime": "2026-10-15T00:00"},
        },
        "assignments": {
            "assignment-1": {
                "course_source_repo": "a-f2026",
                "due_datetime": "2026-10-13",
            }
        },
        "events": {
            "final": {"kind": "exam", "title": "Final", "event_datetime": "2026-12-15"},
            "project-clinic": {
                "title": "Project Clinic",
                "event_datetime": "2026-10-14T10:00",
            },
        },
    }
    sched = parse(meta)
    assert sched.semester_start == date(2026, 9, 7)
    assert [r.label for r in sched.releases] == [
        "session_2",
        "a1-handout",
    ]  # sorted by when
    s2 = sched.releases[0]
    assert s2.deploy == [
        Deploy("cm-f2026", "lectures/02_intro", "materials", "lectures/02_intro")
    ]
    assert sched.releases[1].is_event_only
    assert (
        sched.assignments["assignment-1"]
        .due_datetime.isoformat()
        .startswith("2026-10-13T23:59:59")
    )
    # The late cutoff is computed: the due date plus the institution's 10-day window.
    assert (
        schedule.grading_cutoff_datetime(sched, "assignment-1")
        .isoformat()
        .startswith("2026-10-23T23:59:59")
    )
    # events are display-only rows, in calendar order; `type` defaults to special_event
    assert sched.events == [
        Event(
            label="project-clinic",
            title="Project Clinic",
            when=datetime(2026, 10, 14, 10, 0, tzinfo=BERLIN),
            kind="special_event",
        ),
        Event(label="final", title="Final", when=date(2026, 12, 15), kind="exam"),
    ]


def test_parse_empty_is_safe():
    assert parse({}) == Schedule()
    assert parse(None) == Schedule()


def test_release_without_when_is_dropped():
    meta = {
        "releases": {
            "ok": {"event_datetime": "2026-09-01", "deploy": []},
            "nope": {"deploy": []},
        }
    }
    assert [r.label for r in parse(meta).releases] == ["ok"]


def test_deploy_accepts_single_mapping_defaults_semester_dest_path_none():
    meta = {
        "releases": {
            "s": {
                "event_datetime": "2026-09-01",
                "deploy": {
                    "course_source_repo": "cm",
                    "course_source_path": "lectures/00_x",
                },
            }
        }
    }
    assert parse(meta).releases[0].deploy == [
        Deploy("cm", "lectures/00_x", "materials", None)
    ]


def test_deploy_entry_missing_source_is_skipped():
    meta = {
        "releases": {
            "s": {
                "event_datetime": "2026-09-01",
                "deploy": [{"course_source_repo": "cm"}, {"course_source_path": "x"}],
            }
        }
    }
    assert parse(meta).releases[0].deploy == []


def test_deploy_entry_using_the_old_unprefixed_keys_is_skipped():
    # The org prefixes are a hard rename with no alias handling, so a semester whose
    # schedule.yml predates it must lose the copy outright rather than half-parse it.
    meta = {
        "releases": {
            "s": {
                "event_datetime": "2026-09-01",
                "deploy": [
                    {
                        "source_repo": "cm",
                        "source_path": "lectures/00_x",
                        "dest_repo": "materials",
                    }
                ],
            }
        }
    }
    assert parse(meta).releases[0].deploy == []


def test_event_bare_date_stays_a_date_timed_event_becomes_aware_datetime():
    # `event_datetime:` doubles as "whole day" (a plain date) and "starts at" (a
    # datetime) - the website renders its placeholder time only for the former, so the
    # two must not collapse into one type.
    sched = parse(
        {
            "events": {
                "mid-term": {"kind": "exam", "event_datetime": "2026-11-03"},
                "final": {"kind": "exam", "event_datetime": "2026-12-15T14:00"},
            }
        }
    )
    midterm, final = sched.events
    assert midterm.when == date(2026, 11, 3)
    assert not isinstance(midterm.when, datetime)
    assert final.when == datetime(2026, 12, 15, 14, 0, tzinfo=BERLIN)
    assert final.when.utcoffset() == BERLIN.utcoffset(datetime(2026, 12, 15, 14, 0))


def test_event_yaml_native_date_and_datetime_objects():
    # PyYAML hands us real date/datetime objects, not strings, for unquoted values.
    sched = parse(
        {
            "events": {
                "whole-day": {"event_datetime": date(2026, 11, 3)},
                "timed": {"event_datetime": datetime(2026, 12, 15, 14, 0)},
            }
        }
    )
    assert sched.events[0].when == date(2026, 11, 3)
    assert sched.events[1].when == datetime(2026, 12, 15, 14, 0, tzinfo=BERLIN)


def test_event_explicit_offset_keeps_its_instant_and_is_stored_in_the_semester_tz():
    sched = parse(
        {
            "timezone": "Europe/Berlin",
            "events": {"remote": {"event_datetime": "2026-12-15T14:00+00:00"}},
        }
    )
    when = sched.events[0].when
    assert when == datetime(2026, 12, 15, 14, 0, tzinfo=ZoneInfo("UTC"))  # same instant
    assert when.hour == 15 and when.tzinfo == BERLIN  # ...on the semester's clock (CET)


def test_event_timezone_comes_from_the_semester_setting():
    sched = parse(
        {
            "timezone": "Pacific/Niue",
            "events": {"e": {"event_datetime": "2026-12-15T14:00"}},
        }
    )
    assert sched.events[0].when.tzinfo == ZoneInfo("Pacific/Niue")


def test_event_without_a_usable_date_is_dropped():
    assert (
        parse(
            {"events": {"no-date": {"title": "X"}, "bad": {"event_datetime": "soon"}}}
        ).events
        == []
    )


def test_event_type_defaults_to_special_event_and_rejects_unknown_values():
    meta = {
        "events": {
            "mid-term": {"kind": "Exam", "event_datetime": "2026-11-03"},
            "clinic": {"event_datetime": "2026-10-14T10:00"},
            "typo": {"kind": "examm", "event_datetime": "2026-10-20"},
        }
    }
    events = {e.label: e for e in parse(meta).events}
    assert events["mid-term"].kind == "exam"  # case-normalised
    assert events["clinic"].kind == "special_event"
    assert (
        events["typo"].kind == "special_event"
    )  # unknown value -> the display default


def test_events_sort_by_date_with_undated_last():
    meta = {
        "events": {
            "resit": {"event_datetime": "tbc"},
            "final": {"kind": "exam", "event_datetime": "2026-12-15T14:00"},
            "clinic": {"event_datetime": date(2026, 10, 14)},
        }
    }
    # whole-day and timed entries sort against each other; TBC rows go to the end
    assert [e.label for e in parse(meta).events] == ["clinic", "final", "resit"]


def test_tbc_semantics_for_events():
    meta = {
        "events": {
            "mid-term": {"kind": "exam", "event_datetime": "2026-11-03", "tbc": True},
            "resit": {"kind": "exam", "event_datetime": "tbc"},
            "broken": {"event_datetime": "not-a-date"},  # no date, no tbc -> dropped
        }
    }
    midterm, resit = parse(meta).events
    assert midterm.tbc and midterm.when == date(2026, 11, 3)  # provisional, but dated
    assert resit.tbc and resit.when is None


def test_assignment_bare_date_is_rejected_only_the_nested_form_is_accepted():
    # `assignments: {slug: date}` (no nested due_datetime) is not the documented schema.
    assert parse({"assignments": {"assignment-1": "2026-10-13"}}).assignments == {}


def test_assignment_without_due_is_skipped():
    assert parse({"assignments": {"assignment-1": {"title": "x"}}}).assignments == {}


def test_event_datetime_is_the_only_accepted_key():
    meta = {
        "releases": {
            "new-style": {"event_datetime": "2026-09-15T10:00", "deploy": []},
            "old-alias": {"calendar_event": "2026-09-01T09:00", "deploy": []},
            "older-alias": {"when": "2026-08-01T09:00", "deploy": []},
        }
    }
    releases = {r.label: r for r in parse(meta).releases}
    assert list(releases) == ["new-style"]
    assert releases["new-style"].when.isoformat().startswith("2026-09-15T10:00")


def test_deploy_datetime_parses_and_defaults_to_none():
    meta = {
        "releases": {
            "session_2": {
                "event_datetime": "2026-09-15T10:00",
                "deploy": [
                    {
                        "course_source_repo": "cm-f2026",
                        "course_source_path": "lectures/02_intro",
                        "deploy_datetime": "2026-09-15T09:00",
                    },
                    {
                        "course_source_repo": "cm-f2026",
                        "course_source_path": "readings/02_intro",
                    },
                ],
            }
        }
    }
    (r,) = parse(meta).releases
    early, at_class = r.deploy
    assert early.deploy_datetime.isoformat().startswith("2026-09-15T09:00")
    assert at_class.deploy_datetime is None
    # due_deploys: the early copy fires before the class, the other at it
    tz = early.deploy_datetime.tzinfo
    between = datetime(2026, 9, 15, 9, 30, tzinfo=tz)
    assert r.due_deploys(between) == [early]
    assert r.due_deploys(datetime(2026, 9, 15, 10, 0, tzinfo=tz)) == [early, at_class]


def test_display_only_entry_is_kept_with_its_title():
    meta = {
        "releases": {
            "project-clinic": {
                "event_datetime": "2026-11-17T10:00",
                "title": "Project clinic",
            }
        }
    }
    (r,) = parse(meta).releases
    assert r.is_event_only and r.title == "Project clinic"


def test_malformed_deploy_datetime_falls_back_to_the_event_datetime():
    meta = {
        "releases": {
            "s": {
                "event_datetime": "2026-09-15T10:00",
                "deploy": [
                    {
                        "course_source_repo": "cm-f2026",
                        "course_source_path": "lectures/02_intro",
                        "deploy_datetime": "not-a-date",
                    }
                ],
            }
        }
    }
    (r,) = parse(meta).releases
    assert r.deploy[0].deploy_datetime is None  # ships at the event_datetime


def test_the_settings_that_moved_to_grading_config_are_flagged_by_name():
    # The clean break. A semester still carrying `type: group` is not making a typo, it is
    # declaring the shape in a file that no longer reads it - so the entry is KEPT (its
    # dates are still good) and the message says which file the declaration moved to.
    meta = {
        "assignments": {
            "assignment-4-project": {
                "course_source_repo": "a-f2026-1",
                "due_datetime": "2026-11-15",
                "type": "group",
                "max_team_size": 3,
            },
        }
    }
    sched = parse(meta)
    entry = sched.assignments["assignment-4-project"]
    assert not hasattr(entry, "type") and not hasattr(entry, "max_team_size")
    assert entry.due_datetime is not None  # the timing it DID declare still stands
    kinds = {line.split(":")[0] for line in sched.dropped}
    assert kinds == {
        "assignments.assignment-4-project.type",
        "assignments.assignment-4-project.max_team_size",
    }
    for line in sched.dropped:
        assert "grading_config.yml" in line and "solution" in line
        # named as MOVED, not as the generic typo they are not
        assert "unrecognised key" not in line


def test_assignment_handout_parses():
    meta = {
        "assignments": {
            "assignment-1": {
                "course_source_repo": "a-f2026-1",
                "due_datetime": "2026-10-13",
                "handout_datetime": "2026-09-22T09:00",
            },
            "assignment-2": {
                "course_source_repo": "a-f2026-2",
                "due_datetime": "2026-11-10",
            },
        }
    }
    entries = parse(meta).assignments
    assert (
        entries["assignment-1"]
        .handout_datetime.isoformat()
        .startswith("2026-09-22T09:00")
    )
    assert entries["assignment-2"].handout_datetime is None


def test_assignment_solution_datetime_parses_and_has_no_default():
    # The model solution has no fallback date on purpose: shipping it the moment
    # submissions close rewards anyone who pushes late, so an omitted key must mean
    # "never automatically", not "at the due date".
    meta = {
        "assignments": {
            "assignment-1": {
                "course_source_repo": "a-f2026-1",
                "due_datetime": "2026-10-13",
                "handout_datetime": "2026-09-22T09:00",
                "solution_datetime": "2026-10-16T09:00",
            },
            "assignment-2": {
                "course_source_repo": "a-f2026-2",
                "due_datetime": "2026-11-10",
            },
        }
    }
    entries = parse(meta).assignments
    assert (
        entries["assignment-1"]
        .solution_datetime.isoformat()
        .startswith("2026-10-16T09:00")
    )
    assert entries["assignment-2"].solution_datetime is None
    # and a valid one raises no "unrecognised key" noise
    assert not any("unrecognised key" in d for d in parse(meta).dropped)


def test_a_solution_datetime_without_a_handout_is_flagged_at_parse_time():
    # The scheduler cannot carry a solution without a handout release to put it on, and
    # the only other symptom is a solution that silently never ships. So the contradiction
    # is reported on the commit that introduces it, via --validate, not from a cron log.
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "solution_datetime": "2026-10-16T09:00",
                }
            }
        }
    )
    assert any("solution_datetime" in d for d in sched.dropped)
    assert any("needs `handout_datetime` set too" in d for d in sched.dropped)
    assert sched.assignments["assignment-1"].solution_datetime is None
    # the entry itself survives - only the automatic solution release is lost
    assert sched.assignments["assignment-1"].due_datetime is not None


def test_a_solution_datetime_not_after_the_handout_is_refused():
    # The one unrecoverable mistake this feature can make: a date at or before the handout
    # pushes the model solution into every student repo on the FIRST firing, shipping the
    # answers with the questions. No later run can take that back, so it is refused rather
    # than flagged-and-honoured.
    for bad in ("2026-09-22T09:00", "2026-09-01T09:00"):
        sched = parse(
            {
                "assignments": {
                    "assignment-1": {
                        "course_source_repo": "a-f2026",
                        "due_datetime": "2026-10-13",
                        "handout_datetime": "2026-09-22T09:00",
                        "solution_datetime": bad,
                    }
                }
            }
        )
        assert sched.assignments["assignment-1"].solution_datetime is None, bad
        assert any("not AFTER handout_datetime" in d for d in sched.dropped), bad
        # the assignment itself is untouched - only the automatic solution is withheld
        assert sched.assignments["assignment-1"].handout_datetime is not None


def test_a_solution_datetime_after_the_handout_is_kept():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "handout_datetime": "2026-09-22T09:00",
                    "solution_datetime": "2026-09-22T09:01",
                }
            }
        }
    )
    assert sched.assignments["assignment-1"].solution_datetime is not None
    assert not sched.dropped


def test_an_unparseable_solution_datetime_is_flagged_with_what_it_costs():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "handout_datetime": "2026-09-22T09:00",
                    "solution_datetime": "the friday after",
                }
            }
        }
    )
    # The entry survives - only the solution release is lost, and the drop says so.
    assert sched.assignments["assignment-1"].solution_datetime is None
    assert any("solution_datetime" in d for d in sched.dropped)
    assert any("NEVER ships automatically" in d for d in sched.dropped)


def test_tbc_event_datetime_keeps_an_undated_entry():
    meta = {
        "releases": {
            "guest-lecture": {"event_datetime": "tbc", "title": "Guest lecture"},
            "dated": {"event_datetime": "2026-09-15T10:00", "deploy": []},
            "dropped": {"deploy": []},  # no date, no tbc -> gone
        }
    }
    releases = parse(meta).releases
    assert [r.label for r in releases] == ["dated", "guest-lecture"]  # TBC sorts last
    gl = releases[-1]
    assert gl.when is None and gl.tbc and gl.is_event_only
    # undated -> nothing can ever be due
    assert gl.due_deploys(datetime(2099, 1, 1, tzinfo=ZoneInfo("UTC"))) == []


def test_tbc_flag_keeps_a_provisional_date_firing():
    meta = {
        "releases": {
            "clinic": {"event_datetime": "2026-11-17T10:00", "tbc": True},
        },
    }
    (clinic,) = parse(meta).releases
    assert clinic.tbc and clinic.when is not None  # provisional: still fires


def test_show_on_site_defaults_true_and_only_an_explicit_false_silences_an_entry():
    # Default true, so a plan written before the key existed still announces every entry.
    # Only a literal `false` opts out - a missing key, or anything truthy, shows the row,
    # and the entry deploys either way.
    meta = {
        "releases": {
            "lecture-1": {"event_datetime": "2026-09-01T10:00"},
            "readings-1": {"event_datetime": "2026-08-25T09:00", "show_on_site": False},
            "lab-1": {"event_datetime": "2026-09-03T14:00", "show_on_site": True},
        },
    }
    by_label = {r.label: r for r in parse(meta).releases}
    assert by_label["lecture-1"].show_on_site is True
    assert by_label["readings-1"].show_on_site is False
    assert by_label["lab-1"].show_on_site is True


def test_show_on_site_is_a_known_release_key():
    # An unrecognised key is FLAGGED rather than silently ignored, so a schedule that
    # opts out of the site must not read as a typo faculty are told to fix.
    meta = {
        "releases": {
            "readings-1": {"event_datetime": "2026-08-25T09:00", "show_on_site": False},
        },
    }
    assert parse(meta).dropped == []


def test_insert_handout_records_write_once():
    from dsl_course.schedule import _insert_handout

    base = """timezone: Europe/Berlin

assignments:
  assignment-1:
    due_datetime: 2026-10-13
  assignment-2:
    handout_datetime: 2026-09-29T14:00
    due_datetime: 2026-10-27
"""
    # inserted into the existing entry, directly under the slug line
    out = _insert_handout(base, "assignment-1", "2026-09-22T14:05")
    assert "handout_datetime: 2026-09-22T14:05" in out
    assert out.index("assignment-1:") < out.index("handout_datetime: 2026-09-22T14:05")
    # write-once: an existing handout (scheduled or recorded) is never touched
    assert _insert_handout(base, "assignment-2", "2026-10-01T00:00") is None
    # unknown slug: appended into the block with a due_datetime TODO
    out = _insert_handout(base, "assignment-9", "2026-11-01T09:00")
    assert "assignment-9:" in out and "handout_datetime: 2026-11-01T09:00" in out
    assert "TODO" in out
    # no assignments block at all: one is created
    out = _insert_handout(
        "timezone: Europe/Berlin\n", "assignment-1", "2026-09-22T14:05"
    )
    assert "assignments:" in out and "handout_datetime: 2026-09-22T14:05" in out


def test_record_handout_round_trips_through_the_parser(monkeypatch):
    import yaml

    from dsl_course import schedule as S

    store = {
        "text": "assignments:\n  assignment-1:\n    course_source_repo: a-f2026\n    due_datetime: 2026-10-13\n"
    }
    monkeypatch.setattr(
        S, "get_file_with_sha", lambda org, repo, path: (store["text"], "sha0")
    )
    writes = []
    monkeypatch.setattr(
        "dsl_course.schedule.put_file",
        lambda org, repo, path, content, msg, expected_sha=None: (
            writes.append(content.decode()) or True
        ),
    )
    S.record_handout("Semester-f2026", "assignment-1", "2026-09-22T14:05")
    (new,) = writes
    sched = S.parse(yaml.safe_load(new))
    assert (
        sched.assignments["assignment-1"]
        .handout_datetime.isoformat()
        .startswith("2026-09-22T14:05")
    )
    # second call sees the recorded value and is a no-op
    store["text"] = new
    S.record_handout("Semester-f2026", "assignment-1", "2026-09-23T09:00")
    assert len(writes) == 1


def test_the_plan_is_read_once_per_semester_and_a_handout_reopens_it(monkeypatch):
    # An hourly tick reads the plan in the scheduler and again inside every handout and
    # collection it fires - one GET each, for a file that only a person or record_handout
    # changes. record_handout IS that writer, so it drops the memo.
    reads: list[str] = []
    monkeypatch.setattr(
        schedule,
        "get_file_content",
        lambda org, repo, path: reads.append(org) or "timezone: Europe/Berlin\n",
    )
    schedule.load("Semester-f2026")
    schedule.load("Semester-f2026")
    assert reads == ["Semester-f2026"], "the plan was re-read within one run"

    schedule.load("Semester-f2027")
    assert len(reads) == 2, "one semester's plan answered for another"

    monkeypatch.setattr(schedule, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(
        schedule, "get_file_with_sha", lambda org, repo, path: ("", "sha0")
    )
    schedule.record_handout("Semester-f2026", "assignment-1", "2026-09-22T14:05")
    schedule.load("Semester-f2026")
    assert len(reads) == 3, "the memo survived a write to schedule.yml"


# ------------------------------------------------ keys that left schedule.yml (0009)


def test_a_leftover_enrolment_block_is_not_migrated():
    sched = parse(
        {
            "semester_start": "2026-09-07",
            "enrolment": {"send_codes_datetime": "2026-08-24T08:00"},
        }
    )
    (fault,) = sched.faults
    assert fault.code == faults_module.NOT_MIGRATED and fault.field == "enrolment"
    assert sched.semester_start == date(2026, 9, 7)  # the rest of the file is read


def test_a_leftover_assignment_title_is_faulted_and_the_entry_kept():
    sched = parse(
        {
            "assignments": {
                "a1": {
                    "course_source_repo": "a",
                    "due_datetime": "2026-10-13",
                    "title": "Regression",
                }
            }
        }
    )
    assert set(sched.assignments) == {"a1"}  # display-only: nothing depends on it
    (fault,) = sched.faults
    assert fault.code == faults_module.NOT_MIGRATED and fault.field == "title"


@pytest.mark.parametrize("key", ["grading_datetime", "semester_dest_repo"])
def test_an_assignment_carrying_a_key_that_left_is_not_migrated(key):
    sched = parse(
        {
            "assignments": {
                "a1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    key: "2026-10-15",
                }
            }
        }
    )
    # Dropped whole: read without it, the entry would grade to another cutoff or hand
    # out into repos of another name.
    assert sched.assignments == {}
    (fault,) = sched.faults
    assert fault.code == faults_module.NOT_MIGRATED and fault.field == key
    assert "entry dropped: no hand out, freeze or grading until fixed" in fault.what


def test_a_release_that_hands_out_is_not_migrated_and_still_deploys():
    sched = parse(
        {
            "releases": {
                "s1": {
                    "event_datetime": "2026-10-15T00:00",
                    "assignment": "assignment-1-f2026",
                    "deploy": [{"course_source_repo": "cm", "course_source_path": "x"}],
                }
            }
        }
    )
    (release,) = sched.releases
    assert release.assignment is None and release.deploy
    (fault,) = sched.faults
    assert fault.code == faults_module.NOT_MIGRATED and fault.field == "assignment"


def test_a_genuinely_unknown_top_level_key_is_still_flagged(capsys):
    # The tolerance is for one named key, not a hole in the unknown-key check - a whole
    # plan under `materials_releases:` must still fail validation.
    sched = parse({"materials_releases": {"lecture_02": {}}})
    assert [d.split(":")[0] for d in sched.dropped] == ["materials_releases"]
    assert "DEPRECATED" not in capsys.readouterr().out


# --------------------------------------------------------- a file that does not parse
#
# The incident: a faculty member left an unclosed flow mapping in schedule.yml, so
# `yaml.safe_load` raised inside `schedule.load` and took down BOTH the hourly Scheduled
# release run AND Sync site for that semester - the site kept showing the template's
# placeholders. `load` now treats an unparseable file exactly as an absent one (empty
# Schedule) and says so loudly.
#
# NB the literal below is the incident's flow mapping. tests/ is out of scope for the
# block-style guard (tests/test_yaml_block_style.py sweeps dsl_course/*.py, the repo's
# *.yml and the docs' yaml fences), and this is a malformed counter-example, not a
# faculty-facing example to copy.
MALFORMED_SCHEDULE = """\
materials_releases:
  lab-1:
    event_datetime: 2026-09-03T14:00
    deploy:
      - {course_source_repo: course-materials-f2026,
        course_source_path: labs/01_lab
"""


def test_unparseable_schedule_loads_as_empty_and_says_so_loudly(monkeypatch, capsys):
    from dsl_course import schedule as S

    monkeypatch.setattr(
        S, "get_file_content", lambda org, repo, path: MALFORMED_SCHEDULE
    )

    sched = S.load("Semester-f2026")

    # same shape a missing schedule.yml yields - nothing scheduled, nothing raised - but
    # flagged, and carrying the one fault that says so (see the test below)
    assert sched.unparseable and not sched.releases and not sched.assignments
    err = capsys.readouterr().err
    # self-diagnosing: which semester, which file, the parser's own line/column, what to do
    assert "Semester-f2026/semester-config/schedule.yml is NOT valid YAML" in err
    assert "line 5" in err and "flow mapping" in err
    assert "fix semester-config/schedule.yml on main" in err
    assert "NOTHING is scheduled" in err


def test_a_wellformed_schedule_is_untouched_by_the_yaml_guard(monkeypatch, capsys):
    from dsl_course import schedule as S

    good = (
        "timezone: Europe/Berlin\n"
        "semester_start: 2026-09-07\n"
        "releases:\n"
        "  lab-1:\n"
        "    event_datetime: 2026-09-03T14:00\n"
        "    deploy:\n"
        "      - course_source_repo: course-materials-f2026\n"
        "        course_source_path: labs/01_lab\n"
    )
    monkeypatch.setattr(S, "get_file_content", lambda org, repo, path: good)

    sched = S.load("Semester-f2026")

    assert sched.semester_start == date(2026, 9, 7)
    assert [r.label for r in sched.releases] == ["lab-1"]
    assert sched.releases[0].deploy[0].course_source_path == "labs/01_lab"
    assert capsys.readouterr().err == ""


def test_a_non_mapping_schedule_still_loads_as_empty(monkeypatch, capsys):
    # parses fine, but isn't a mapping - the pre-existing isinstance guard, pinned here
    # so the new try/except can't be mistaken for the only defence. Same consequence as a
    # parse failure (nothing in the file is read), so it carries the same flag.
    from dsl_course import schedule as S

    monkeypatch.setattr(
        S, "get_file_content", lambda org, repo, path: "- just\n- a list\n"
    )
    sched = S.load("Semester-f2026")
    assert sched.unparseable and not sched.releases
    (fault,) = sched.faults
    assert fault.what.startswith("this file parses as list, not a mapping")
    assert fault.lineno is None  # nothing in the file was read: no line to cite
    assert "not a mapping" in capsys.readouterr().err


def test_an_unparseable_plan_is_one_fault_not_an_empty_plan(monkeypatch):
    # A file nobody can parse is the commonest way faculty break this file, and the
    # costliest: every entry is out of the plan. It used to reach nobody through the
    # notification engine - the digest issue saw an empty fault list and CLOSED, while
    # the hourly cron went red at a bot account. One immediate fault on the file itself,
    # citing the line the parser stopped on.
    from dsl_course import schedule as S

    monkeypatch.setattr(
        S, "get_file_content", lambda org, repo, path: MALFORMED_SCHEDULE
    )

    (fault,) = S.load("Semester-f2026").faults

    assert fault.where == fault.file == S.SCHEDULE_PATH
    assert fault.field == "schedule"
    assert fault.fires is None  # immediate: waiting changes nothing about it
    # the line PyYAML stopped on, from the same helper every other reader cites
    # (`gh_contents.yaml_mark_line`): the unclosed mapping runs to the end of the file
    assert fault.lineno == 7 and fault.at == "schedule.yml:7"
    assert "not valid YAML" in fault.what and "nothing releases" in fault.what
    assert "every entry is ignored until the file parses" in fault.fix_text


def test_a_comment_only_schedule_is_empty_not_unparseable(monkeypatch, capsys):
    # `yaml.safe_load` returns None for a file of nothing but comments. That is an empty
    # plan, exactly like an absent file - not a fault to redden the hourly cron with.
    from dsl_course import schedule as S

    monkeypatch.setattr(
        S, "get_file_content", lambda org, repo, path: "# nothing yet\n"
    )
    assert S.load("Semester-f2026") == Schedule()
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------- dropped-entry reporting
# A malformed entry cannot be rescued, but it must never vanish quietly: valid YAML with a
# typo'd key is the one schedule fault that leaves a green run and a short term plan.


def test_every_kind_of_dropped_entry_is_recorded_with_its_cost():
    sched = parse(
        {
            "releases": {
                "ok": {
                    "event_datetime": "2026-09-15T10:00",
                    "deploy": [
                        {"course_source_repo": "cm", "course_source_path": "l/01"},
                        {"source_repo": "cm", "source_path": "l/02"},  # pre-rename keys
                    ],
                },
                "typo": {"evetn_datetime": "2026-09-22T10:00"},
            },
            "assignments": {
                "a1": {"course_source_repo": "a-f2026", "due_datetime": "2026-10-13"},
                "a2": {"due_date": "2026-11-13"},
            },
            "events": {"mid-term": {"kind": "exam"}},
        }
    )
    # the well-formed entries still parse - one bad entry never poisons its neighbours
    assert [r.label for r in sched.releases] == ["ok"]
    assert len(sched.releases[0].deploy) == 1
    assert list(sched.assignments) == ["a1"]

    where = [d.split(":")[0] for d in sched.dropped]
    assert where == [
        "releases.ok.deploy[1]",
        "releases.typo",
        "assignments.a2",
        "events.mid-term",
    ]
    # each line names the field at fault AND what the semester loses by it
    assert (
        "`course_source_repo`" in sched.dropped[0] and "never ships" in sched.dropped[0]
    )
    assert (
        "`event_datetime`" in sched.dropped[1] and "nothing deploys" in sched.dropped[1]
    )
    assert "`due_datetime`" in sched.dropped[2] and "no autograding" in sched.dropped[2]
    assert (
        "`event_datetime`" in sched.dropped[3] and "never appears" in sched.dropped[3]
    )


def test_an_assignment_without_a_course_source_repo_is_dropped():
    # Required, like due_datetime: the repo is never guessed from the slug, so an entry
    # that does not name one has nothing to hand out and no way to be graded.
    sched = parse(
        {
            "assignments": {
                "ok": {"course_source_repo": "a-f2026", "due_datetime": "2026-10-13"},
                "no-repo": {"due_datetime": "2026-10-20"},
                "blank-repo": {
                    "course_source_repo": "  ",
                    "due_datetime": "2026-10-27",
                },
            }
        }
    )
    assert list(sched.assignments) == ["ok"]
    assert [d.split(":")[0] for d in sched.dropped] == [
        "assignments.no-repo",
        "assignments.blank-repo",
    ]
    assert "`course_source_repo`" in sched.dropped[0]
    assert "no autograding" in sched.dropped[0]


def test_semester_dest_repo_comes_from_assignments_yml_and_defaults_to_the_slug():
    from dsl_course.schedule import semester_name

    sched = parse(
        {
            "assignments": {
                "hw": {"course_source_repo": "a-f2026-1", "due_datetime": "2026-10-13"},
                "named": {
                    "course_source_repo": "a-f2026-2",
                    "due_datetime": "2026-10-20",
                },
                "blank": {
                    "course_source_repo": "a-f2026-3",
                    "due_datetime": "2026-10-27",
                },
            }
        },
        _instance(
            "assignments:\n  named:\n    semester_dest_repo: homework-1\n"
            "  blank:\n    semester_dest_repo: '  '\n"
        ),
    )
    # unset (and blank) -> the slug IS the semester-side name; set -> it wins
    assert semester_name("hw", sched.assignments["hw"]) == "hw"
    assert semester_name("named", sched.assignments["named"]) == "homework-1"
    assert semester_name("blank", sched.assignments["blank"]) == "blank"


def test_entry_for_repo_matches_on_course_source_repo_not_the_slug():
    from dsl_course.schedule import entry_for_repo

    sched = parse(
        {
            "assignments": {
                "regression": {
                    "course_source_repo": "wk3-regression-f2026",
                    "due_datetime": "2026-11-10",
                }
            }
        }
    )
    # the slug is a free label, so consumers that start from a REPO name must match on
    # course_source_repo - deriving a slug from the repo would miss this entry entirely
    found = entry_for_repo(sched, "wk3-regression-f2026")
    assert found is not None and found[0] == "regression"
    assert entry_for_repo(sched, "regression-f2026") is None


def test_an_unknown_timezone_is_reported_rather_than_silently_swapped():
    sched = parse(
        {"timezone": "Europe/Berlyn", "events": {"e": {"event_datetime": "2026-11-03"}}}
    )
    assert len(sched.dropped) == 1
    assert "Europe/Berlyn" in sched.dropped[0] and "Europe/Berlin" in sched.dropped[0]
    assert sched.events[0].when == date(2026, 11, 3)  # the event itself survives


@pytest.mark.parametrize("key", ["semester_start", "semester_end"])
def test_an_unparseable_term_date_is_reported_not_silently_synthesised(key):
    # `01/09/2026` coerces to None exactly like an absent key, and the site then
    # synthesises term dates - shifting every weekly session row, green.
    sched = parse({key: "01/09/2026"})
    assert getattr(sched, key) is None
    assert len(sched.dropped) == 1
    # top-level: the location renders bare, not as a stray-dotted `.semester_start`
    assert sched.dropped[0].startswith(f"{key}: unusable value")
    assert "shifting every session row" in sched.dropped[0]


@pytest.mark.parametrize("key", ["semester_start", "semester_end"])
def test_an_absent_term_date_is_not_flagged(key):
    # Absent is a legitimate "not declared" - only a value faculty wrote and we cannot
    # read is a fault.
    assert parse({}).dropped == []
    assert parse({key: date(2026, 9, 1)}).dropped == []


def test_a_dangling_deploy_key_is_flagged_but_an_explicit_empty_list_is_not():
    # `deploy:` with nothing under it parses to None, indistinguishable from the key being
    # absent by the time _parse_deploy sees it - and absent is legitimate (a display-only
    # session row). Both demo semesters carried three of these on live sessions, each looking
    # for all the world like it should ship something.
    def drops(entry):
        return parse({"releases": {"lecture-9": entry}}).dropped

    when = {"event_datetime": "2026-09-29"}
    assert drops(when) == []  # absent: a deliberate display-only row
    assert drops({**when, "deploy": []}) == []  # explicit "no copies", left alone
    flagged = drops({**when, "deploy": None})
    assert len(flagged) == 1
    assert "ships NOTHING" in flagged[0]
    # the entry itself survives as the display-only row it in fact is
    (r,) = parse({"releases": {"lecture-9": {**when, "deploy": None}}}).releases
    assert r.deploy == []
    assert r.is_event_only


def test_a_clean_schedule_drops_nothing():
    assert parse({}).dropped == []
    assert (
        parse(
            {
                "releases": {"s": {"event_datetime": "2026-09-01", "deploy": []}},
                "assignments": {
                    "a1": {
                        "course_source_repo": "a-f2026",
                        "due_datetime": "2026-10-13",
                    }
                },
                "events": {"e": {"event_datetime": "2026-11-03"}},
            }
        ).dropped
        == []
    )


def test_tbc_entries_are_not_drops():
    # `tbc` is a deliberate "date not settled yet", not a malformed date
    sched = parse(
        {
            "releases": {"r": {"event_datetime": "tbc"}},
            "events": {"guest": {"event_datetime": "tbc"}},
        }
    )
    assert sched.dropped == []
    assert len(sched.releases) == 1 and len(sched.events) == 1


def test_load_logs_every_dropped_entry_loudly(monkeypatch, capsys):
    from dsl_course import schedule as S

    monkeypatch.setattr(
        S,
        "get_file_content",
        lambda org, repo, path: (
            "assignments:\n  assignment-2:\n    due_date: 2026-11-13\n"
        ),
    )

    sched = S.load("Semester-f2026")

    assert sched.assignments == {}
    err = capsys.readouterr().err
    # which semester, which file, which entry, which field, and what it costs
    assert "Semester-f2026/semester-config/schedule.yml" in err
    assert "DROPPED" in err
    assert "assignments.assignment-2" in err
    assert "`due_datetime`" in err
    assert "no autograding" in err


# ------------------------------------------------------- validating a file on disk (CI)


def test_load_file_reports_unparseable_yaml_rather_than_treating_it_as_empty(tmp_path):
    # The opposite stance to `load`: the cron must survive a typo, a validator must fail on
    # one. A broken file that silently validated would be worse than no validator at all.
    bad = tmp_path / "schedule.yml"
    bad.write_text("releases:\n  a:\n    deploy:\n      - {x: 1,\n")
    sched, error = schedule.load_file(str(bad))
    assert sched is None
    assert "not valid YAML" in error and "line" in error


def test_load_file_reports_a_missing_file(tmp_path):
    sched, error = schedule.load_file(str(tmp_path / "nope.yml"))
    assert sched is None and "cannot read" in error


def test_load_file_rejects_valid_yaml_that_is_not_a_mapping(tmp_path):
    p = tmp_path / "schedule.yml"
    p.write_text("- just\n- a list\n")
    sched, error = schedule.load_file(str(p))
    assert sched is None and "not a mapping" in error


def test_load_file_parses_and_surfaces_drops(tmp_path):
    p = tmp_path / "schedule.yml"
    p.write_text("assignments:\n  assignment-2:\n    due_date: 2026-11-13\n")
    sched, error = schedule.load_file(str(p))
    assert error is None
    assert sched.assignments == {} and len(sched.dropped) == 1


@pytest.mark.parametrize(
    "path",
    [
        "example-course/semester-org/schedule.yml",
        "templates/semester-config/schedule.yml",
    ],
)
def test_shipped_schedules_parse_with_nothing_dropped(path):
    # The CI gate. The example is what faculty copy and the template is what every new
    # semester is seeded with, so either one silently dropping an entry would teach the
    # mistake rather than catch it.
    full = Path(__file__).resolve().parents[1] / path
    sched, error = schedule.load_file(str(full))
    assert error is None, error
    assert sched.dropped == [], f"{path} drops entries:\n" + "\n".join(sched.dropped)


def test_the_worked_example_shows_the_archive_block():
    # The sample is what faculty copy; a field only the skeleton mentions is a field nobody
    # sets. Its date is the default spelled out, so the example and the rule agree.
    full = (
        Path(__file__).resolve().parents[1] / "example-course/semester-org/schedule.yml"
    )
    sched, _ = schedule.load_file(str(full))
    assert sched.archive.when == date(2027, 2, 16)
    assert sched.archive.when == sched.semester_end + schedule.ARCHIVE_GRACE
    assert sched.archive.show_on_site is True
    # And it asks for its date by name rather than typing it twice - the habit the
    # example is there to teach (`site._archive_entry` fills the token in).
    assert "{date}" in sched.archive.details


# ------------------------------------- a block authored as a list (never-raise contract)
# `parse` iterates `.items()` over each block; a block written as a YAML LIST or scalar (a
# common mistake - `deploy:` right below IS a list) would raise `AttributeError` and break
# `load`'s promise never to raise, freezing the hourly scheduler AND the site sync.


def test_a_block_authored_as_a_list_is_dropped_not_raised():
    for block, empty in (("releases", []), ("assignments", {}), ("events", [])):
        sched = parse({block: [{"event_datetime": "2026-09-01"}]})
        assert getattr(sched, block) == empty
        assert any(d.startswith(f"{block}:") for d in sched.dropped)


def test_load_never_raises_when_a_block_is_a_list(monkeypatch, capsys):
    from dsl_course import schedule as S

    monkeypatch.setattr(
        S,
        "get_file_content",
        lambda org, repo, path: "releases:\n  - event_datetime: 2026-09-01\n",
    )
    sched = S.load("Semester-f2026")  # must not raise
    assert sched.releases == []
    assert "DROPPED" in capsys.readouterr().err


# ------------------------------------------------- unknown/typo'd keys at every level
# A typo'd or legacy key is silently ignored, so a file validates while meaning something
# other than what faculty wrote. Flagged (but the entry itself is kept when it can parse).


def test_an_unknown_top_level_key_is_reported_not_silently_zero_releases():
    # The Maths-f2026 incident: a whole plan under `materials_releases:` validated as
    # "OK: nothing dropped" with zero releases.
    sched = parse({"materials_releases": {"lab-1": {"event_datetime": "2026-09-01"}}})
    assert sched.releases == []
    assert len(sched.dropped) == 1
    assert sched.dropped[0].startswith("materials_releases:")
    assert "unrecognised key" in sched.dropped[0]


def test_a_typod_field_within_an_entry_is_reported_but_the_entry_survives():
    sched = parse(
        {
            "releases": {
                "s": {
                    "event_datetime": "2026-09-01",
                    "deploy": [
                        {
                            "course_source_repo": "cm",
                            "course_source_path": "l/01",
                            "dest_repo": "materials",  # legacy key, silently ignored
                        }
                    ],
                }
            },
            "assignments": {
                "a1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "grading_dateime": "2026-10-15",  # typo, grading silently falls back
                }
            },
        }
    )
    # the entries still parse - one stray key never poisons the whole entry
    assert len(sched.releases) == 1 and len(sched.releases[0].deploy) == 1
    assert list(sched.assignments) == ["a1"]
    where = {d.split(":")[0] for d in sched.dropped}
    assert "releases.s.deploy[0].dest_repo" in where
    assert "assignments.a1.grading_dateime" in where


# ------------------------------------------- values that are PRESENT but unusable
# The other half of "valid YAML, wrong plan": the key is spelt right and the entry parses,
# but its value doesn't - so the parser falls back, silently, to something faculty did not
# write. The entry is KEPT (as with a stray key); only the fallback is surfaced.


def test_an_unparseable_handout_datetime_is_flagged_with_what_it_costs():
    # The worst of them: the entry looks scheduled, and nothing is ever provisioned.
    sched = parse(
        {
            "assignments": {
                "a1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "handout_datetime": "2026-13-01T09:00",  # month 13
                }
            }
        }
    )
    entry = sched.assignments["a1"]  # the entry survives - only the handout is lost
    assert entry.handout_datetime is None and entry.due_datetime is not None
    (line,) = sched.dropped
    assert line.startswith("assignments.a1.handout_datetime:")
    assert "2026-13-01T09:00" in line
    assert "NEVER fires" in line and "no student or team repos" in line


def test_the_late_cutoff_is_the_due_date_plus_the_effective_window(monkeypatch):
    sched = parse(
        {
            "assignments": {
                "a1": {"course_source_repo": "a", "due_datetime": "2026-10-13"}
            }
        }
    )
    due = sched.assignments["a1"].due_datetime
    # Nothing declared: the institution's 10 days.
    assert schedule.grading_cutoff_datetime(sched, "a1") == due + timedelta(days=10)
    # The semester's assignments.yml answers first; 0 is no late work at all.
    sched.org = "Sem"
    monkeypatch.setattr(
        settings,
        "_assignments_text",
        lambda org: "assignments:\n  a1:\n    late_window_days: 0\n",
    )
    monkeypatch.setattr(settings, "course_org_for_semester", lambda org: "C")
    assert schedule.grading_cutoff_datetime(sched, "a1") == due
    # A late rule naming only the penalty names no window: no late work either.
    settings.semester_blocks.cache_clear()
    monkeypatch.setattr(
        settings,
        "_assignments_text",
        lambda org: "assignments:\n  a1:\n    late_penalty_per_day: 5%\n",
    )
    assert schedule.grading_cutoff_datetime(sched, "a1") == due
    assert schedule.grading_cutoff_datetime(sched, "unknown") is None


def test_an_unparseable_marks_return_datetime_is_flagged():
    sched = parse(
        {
            "assignments": {
                "a1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "marks_return_datetime": "next tuesday",
                },
                "a2": {
                    "course_source_repo": "b-f2026",
                    "due_datetime": "2026-10-13",
                    "marks_return_datetime": {
                        "event_datetime": "2026-10-01",
                        "show_on_site": True,
                    },
                },
                "a3": {
                    "course_source_repo": "c-f2026",
                    "due_datetime": "2026-10-13",
                    "marks_return_datetime": {"event_datetime": "2026-10-27"},
                    "show_on_site": True,
                },
            }
        }
    )
    assert sched.assignments["a1"].marks_return_datetime is None
    assert sched.assignments["a2"].marks_return_datetime is None  # before it was due
    assert not sched.assignments["a1"].marks_return_on_site
    assert sched.assignments["a2"].marks_return_on_site
    # Its own switch: the entry's `show_on_site` does not show the marks row.
    assert sched.assignments["a3"].marks_return_datetime is not None
    assert not sched.assignments["a3"].marks_return_on_site
    assert [d.split(":")[0] for d in sched.dropped] == [
        "assignments.a1.marks_return_datetime",
        "assignments.a2.marks_return_datetime",
    ]


def test_the_formation_window_runs_from_the_handout_to_the_grading_pin():
    sched = parse(
        {
            "assignments": {
                "a1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "handout_datetime": "2026-09-22T09:00",
                }
            }
        }
    )
    assert schedule.formation_window(sched, "a1") == (
        sched.assignments["a1"].handout_datetime,
        schedule.grading_cutoff_datetime(sched, "a1"),
    )
    # A slug this schedule does not carry gets no window at all, rather than half of one.
    assert schedule.formation_window(sched, "nope") == (None, None)


def test_an_assignment_handed_out_by_hand_has_a_window_that_never_opens():
    # No handout_datetime = a moment nobody wrote down, so there is no hour from which
    # "form your team now" would be true.
    sched = parse(
        {
            "assignments": {
                "a1": {"course_source_repo": "a-f2026", "due_datetime": "2026-10-13"}
            }
        }
    )
    opens, closes = schedule.formation_window(sched, "a1")
    assert opens is None
    assert closes == sched.assignments["a1"].due_datetime + timedelta(days=10)


def test_an_unparseable_deploy_datetime_is_flagged():
    sched = parse(
        {
            "releases": {
                "s": {
                    "event_datetime": "2026-09-15T10:00",
                    "deploy": [
                        {
                            "course_source_repo": "cm-f2026",
                            "course_source_path": "lectures/02_intro",
                            "deploy_datetime": "sept 15th",
                        }
                    ],
                }
            }
        }
    )
    assert sched.releases[0].deploy[0].deploy_datetime is None  # ships at the event
    (line,) = sched.dropped
    assert line.startswith("releases.s.deploy[0].deploy_datetime:")
    assert "event_datetime" in line


def test_an_unknown_event_type_is_flagged_like_an_unknown_assignment_type():
    sched = parse(
        {"events": {"mid-term": {"kind": "exma", "event_datetime": "2026-11-03"}}}
    )
    assert sched.events[0].kind == "special_event"  # the row still shows
    (line,) = sched.dropped
    assert line.startswith("events.mid-term.kind:")
    assert "'exma'" in line and "not an exam" in line


def test_an_absent_optional_value_is_never_flagged():
    # Omitting handout/grading/deploy/solution is the documented way to take their
    # defaults - only a value that IS there and cannot be read is a fault.
    assert (
        parse(
            {
                "releases": {
                    "s": {
                        "event_datetime": "2026-09-01",
                        "deploy": [
                            {"course_source_repo": "cm", "course_source_path": "l/01"}
                        ],
                    }
                },
                "assignments": {
                    "a1": {
                        "course_source_repo": "a-f2026",
                        "due_datetime": "2026-10-13",
                    }
                },
                "events": {"e": {"event_datetime": "2026-11-03"}},
            }
        ).dropped
        == []
    )


def test_schedule_and_course_share_one_date_coercion():
    # The two implementations must not drift: schedule re-exports the canonical one.

    assert schedule._coerce_date is course.coerce_date


# ------------------------------------------------ _insert_handout indentation robustness


def test_insert_handout_finds_a_deeper_indented_entry_and_keeps_its_due_datetime():
    import yaml

    from dsl_course.schedule import _insert_handout

    # A 4-space-nested file: the old code matched only `  slug:` (2 spaces), missed this
    # entry, and fabricated a fake 2-space one that swallowed the real entry - dropping its
    # due_datetime for good (write-once meant it was never repaired).
    base = (
        "assignments:\n"
        "    assignment-1:\n"
        "        course_source_repo: a-f2026\n"
        "        due_datetime: 2026-10-13\n"
    )
    out = _insert_handout(base, "assignment-1", "2026-09-22T14:05")
    assert out.count("assignment-1:") == 1  # the real entry, not a fabricated duplicate
    assert "handout_datetime: 2026-09-22T14:05" in out
    entry = parse(yaml.safe_load(out)).assignments["assignment-1"]
    assert entry.due_datetime.isoformat().startswith("2026-10-13")  # survives the edit
    assert entry.handout_datetime.isoformat().startswith("2026-09-22T14:05")


def test_insert_handout_leaves_an_unrecognisable_flow_block_untouched():
    from dsl_course.schedule import DECLINED, _insert_handout

    # A flow-style `assignments: {...}` can't take a line insertion - leave it untouched
    # rather than fabricate a duplicate key. DECLINED, not None: nothing was recorded.
    flow = "assignments: {assignment-1: {due_datetime: 2026-10-13}}\n"
    assert _insert_handout(flow, "assignment-1", "2026-09-22T14:05") is DECLINED


def test_insert_handout_leaves_an_inline_flow_value_entry_untouched():
    from dsl_course.schedule import DECLINED, _insert_handout

    # `assignments:` is a block header but the slug itself is authored as an inline flow
    # value: there's no block body to append into, so leaving it alone is correct - the
    # old scan missed the slug and fabricated a duplicate key that swallowed the real one.
    base = "assignments:\n  assignment-1: {due_datetime: 2026-10-13}\n"
    assert _insert_handout(base, "assignment-1", "2026-09-22T14:05") is DECLINED


def test_insert_handout_distinguishes_declining_from_the_write_once_no_op():
    from dsl_course.schedule import _insert_handout

    # Both used to be None, so "already on record" and "the record is LOST" looked
    # identical to record_handout - which then said nothing in either case. The lost case
    # is DECLINED (pinned in the two tests above); the no-op stays None.
    recorded = (
        "assignments:\n"
        "  assignment-1:\n"
        "    handout_datetime: 2026-09-22T14:05\n"
        "    due_datetime: 2026-10-13\n"
    )
    assert _insert_handout(recorded, "assignment-1", "2026-10-01T09:00") is None


def test_record_handout_says_so_loudly_when_the_file_shape_defeats_the_edit(
    monkeypatch, capsys
):
    # Best-effort stays best-effort (no raise, no exit code), but a handout that went out
    # and was recorded NOWHERE must never leave a silent, green run.
    from dsl_course import schedule as S

    flow = "assignments: {assignment-1: {due_datetime: 2026-10-13}}\n"
    monkeypatch.setattr(S, "get_file_with_sha", lambda org, repo, path: (flow, "sha0"))
    monkeypatch.setattr(
        "dsl_course.schedule.put_file",
        lambda *a, **k: pytest.fail("must not write into a shape it cannot parse"),
    )

    S.record_handout("Semester-f2026", "assignment-1", "2026-09-22T14:05")

    err = capsys.readouterr().err
    assert "could NOT record the assignment-1 handout" in err
    assert "2026-09-22T14:05" in err  # the stamp to add by hand
    assert "on record nowhere" in err


def test_record_handout_says_so_loudly_when_the_write_itself_fails(monkeypatch, capsys):
    # Same fault as above, one step later: the edit was fine and the PUT failed. It used
    # to fall off the end of the `if` with nothing logged - green, and the handout on
    # record nowhere.
    from dsl_course import schedule as S

    good = "assignments:\n  assignment-1:\n    due_datetime: 2026-10-13\n"
    monkeypatch.setattr(S, "get_file_with_sha", lambda org, repo, path: (good, "sha0"))
    monkeypatch.setattr("dsl_course.schedule.put_file", lambda *a, **k: False)

    S.record_handout("Semester-f2026", "assignment-1", "2026-09-22T14:05")

    err = capsys.readouterr().err
    assert "could NOT record the assignment-1 handout" in err
    assert "2026-09-22T14:05" in err
    assert "on record nowhere" in err


def test_record_handout_never_reverts_an_edit_made_while_it_ran(monkeypatch):
    # A handout can take minutes; a faculty member editing schedule.yml in that window had
    # their edit silently reverted, because the write re-read the sha and so always won.
    from dsl_course import schedule as S

    before = "assignments:\n  assignment-1:\n    due_datetime: 2026-10-13\n"
    after = before + "  assignment-2:\n    due_datetime: 2026-11-20\n"
    store = {"text": before, "sha": "sha0"}
    monkeypatch.setattr(
        S, "get_file_with_sha", lambda org, repo, path: (store["text"], store["sha"])
    )
    writes: list[str] = []

    def fake_put(org, repo, path, content, msg, expected_sha=None):
        # expected_sha=None is put_file re-reading the sha itself, so the write always
        # wins - which is the bug. A sha that has moved on is refused.
        if expected_sha is not None and expected_sha != store["sha"]:
            return False
        store["text"] = content.decode()
        store["sha"] = f"{store['sha']}+"
        writes.append(store["text"])
        return True

    monkeypatch.setattr("dsl_course.schedule.put_file", fake_put)
    # the edit lands between the read and the write
    original_read = S.get_file_with_sha

    def moving_read(org, repo, path):
        text, sha = original_read(org, repo, path)
        if store["text"] == before:
            store["text"], store["sha"] = after, "sha1"
        return text, sha

    monkeypatch.setattr(S, "get_file_with_sha", moving_read)

    S.record_handout("Semester-f2026", "assignment-1", "2026-09-22T14:05")

    (final,) = writes
    assert "assignment-2" in final, "a concurrent edit was reverted"
    assert "handout_datetime: 2026-09-22T14:05" in final


def test_validate_is_invalid_for_a_semester_plan_that_does_not_parse(
    monkeypatch, capsys
):
    # `--file` returned 1 for a file that does not parse while `--semester-org` printed "OK:
    # nothing dropped" and exited 0 - because `load` hands back an empty Schedule so the
    # hourly cron cannot be frozen by one semester's typo, and the validator read the
    # fallback as a verdict. The two forms answer the same question and must agree.
    monkeypatch.setattr(
        schedule, "get_file_content", lambda org, repo, path: MALFORMED_SCHEDULE
    )
    monkeypatch.setattr(
        "sys.argv", ["schedule", "--semester-org", "Semester-f2026", "--validate"]
    )
    assert schedule.main() == 1
    out = capsys.readouterr()
    assert "INVALID: Semester-f2026/schedule.yml could not be parsed" in out.out
    assert "OK: nothing dropped" not in out.out
    assert "is NOT valid YAML" in out.err  # what load already said, not said again


def test_validate_cli_reports_an_unreadable_semester_schedule(monkeypatch, capsys):
    # An absent schedule.yml is an empty Schedule (valid: nothing planned yet), but a read
    # that failed outright now raises - the CLI turns that into a line and a red run,
    # rather than a traceback or a false "OK: nothing dropped".
    def boom(semester_org):
        raise RuntimeError("could not read Semester-f2026/semester-config/schedule.yml")

    monkeypatch.setattr(schedule, "load", boom)
    monkeypatch.setattr(
        "sys.argv", ["schedule", "--semester-org", "Semester-f2026", "--validate"]
    )
    assert schedule.main() == 1
    assert "could not read" in capsys.readouterr().err


# --------------------------------------------------------- source existence (advisory)


def _org(monkeypatch, trees: dict[str, list[str]]):
    """Fake a course org as {repo: [every path in it]}. A repo absent from `trees` does not
    exist; one mapped to [] exists but is empty."""
    monkeypatch.setattr(schedule, "repo_missing", lambda org, repo: repo not in trees)
    monkeypatch.setattr(schedule, "default_branch", lambda org, repo: "main")
    monkeypatch.setattr(
        schedule,
        "repo_tree",
        lambda org, repo, branch, kind="": tuple(trees.get(repo, [])),
    )


def _release(label, path, repo="cm"):
    return Release(
        label,
        datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
        deploy=[Deploy(repo, path, "materials", None)],
    )


def test_missing_sources_names_the_path_that_will_ship_nothing(monkeypatch):
    _org(monkeypatch, {"cm": ["lectures", "lectures/01_a"]})
    s = Schedule(
        releases=[
            _release("lecture-1", "lectures/01_a"),
            _release("lecture-2", "lectures/02_b"),
        ]
    )
    out = [f.line() for f in schedule.source_faults(s, "Course-Org")]
    assert len(out) == 1
    assert out[0].startswith("releases.lecture-2 -> course_source_path - ")
    # The WHOLE address of the thing that is missing: the notification tells somebody
    # where to push, and `cm/lectures/02_b` alone leaves them guessing which org.
    assert "Course-Org/cm/lectures/02_b does not exist" in out[0]


def test_a_source_withheld_by_a_releaseignore_is_a_fault_at_commit_time(monkeypatch):
    # The file EXISTS, so nothing looks wrong - which is exactly why it is worth saying
    # here. Otherwise faculty find out only from a `::warning::` on a GREEN release run,
    # months later, on the channel nobody opens when the run is green.
    _org(monkeypatch, {"cm": [".releaseignore", "lectures", "lectures/01_a"]})
    monkeypatch.setattr(
        schedule, "get_file_content", lambda org, repo, path, **k: "lectures/01_a\n"
    )
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a")])
    out = [f.line() for f in schedule.source_faults(s, "Course-Org")]
    assert len(out) == 1
    assert out[0].startswith("releases.lecture-1 -> course_source_path - ")
    assert "the files exist but cm/.releaseignore keeps them back" in out[0]


def test_a_withheld_source_never_escalates_past_a_warning(monkeypatch):
    # A MISSING source climbs the whole ladder as its moment nears, and stays at the top
    # after it passes. A WITHHELD source is a decision faculty already made, so it is
    # listed and nothing more: it must never earn anyone a 24h email, or a "this did not
    # ship" for the rest of the term.
    _org(monkeypatch, {"cm": [".releaseignore", "lectures", "lectures/01_a"]})
    monkeypatch.setattr(
        schedule, "get_file_content", lambda org, repo, path, **k: "lectures/01_a\n"
    )
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a")])
    fault = schedule.source_faults(s, "Course-Org")[0]
    # An hour before it fires, and a week after - both capped.
    assert fault.severity(datetime(2026, 9, 8, 9, 0, tzinfo=BERLIN)) is Severity.WARNING
    assert (
        fault.severity(datetime(2026, 9, 15, 9, 0, tzinfo=BERLIN)) is Severity.WARNING
    )


def test_a_missing_source_still_climbs_the_whole_ladder(monkeypatch):
    # The ceiling is per-fault, so capping the withheld one must not soften this.
    _org(monkeypatch, {"cm": ["lectures"]})
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a")])
    fault = schedule.source_faults(s, "Course-Org")[0]
    assert (
        fault.severity(datetime(2026, 9, 8, 9, 0, tzinfo=BERLIN)) is Severity.CRITICAL
    )
    assert fault.severity(datetime(2026, 9, 15, 9, 0, tzinfo=BERLIN)) is Severity.MISSED


def test_an_unreadable_releaseignore_blob_is_passed_over_in_silence(monkeypatch):
    # `get_file_content` raises on any non-404 failure, and this function's contract is
    # that a repo it cannot READ is passed over quietly. Its loudest caller is the
    # commit-time validator, where a transient 429 would otherwise be a traceback on
    # faculty's own push.
    _org(monkeypatch, {"cm": [".releaseignore", "lectures", "lectures/01_a"]})

    def boom(*a, **k):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(schedule, "get_file_content", boom)
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a")])
    # No raise, and the path is not falsely reported as withheld.
    assert schedule.source_faults(s, "Course-Org") == []


def test_a_repo_with_no_releaseignore_reads_no_blobs(monkeypatch):
    # One tree fetch per repo is the existing budget; this must not add a blob read per
    # path to every validate-schedule run in the estate.
    _org(monkeypatch, {"cm": ["lectures", "lectures/01_a"]})
    reads = []
    monkeypatch.setattr(
        schedule,
        "get_file_content",
        lambda org, repo, path, **k: reads.append(path) or None,
    )
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a")])
    assert schedule.source_faults(s, "Course-Org") == []
    assert reads == []


def test_missing_sources_reports_a_repo_that_is_not_there_at_all(monkeypatch):
    _org(monkeypatch, {})
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a", repo="typo-repo")])
    out = [f.line() for f in schedule.source_faults(s, "Course-Org")]
    assert len(out) == 1 and "no repo Course-Org/typo-repo (or it is empty)" in out[0]


def test_missing_sources_checks_an_assignments_template_repo(monkeypatch):
    _org(monkeypatch, {"assignment-1-f2026": ["README.md"]})
    s = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                due_datetime=datetime(2026, 10, 13, 23, 59, tzinfo=BERLIN),
                course_source_repo="assignment-1-f2026",
            ),
            "assignment-2": AssignmentEntry(
                due_datetime=datetime(2026, 10, 27, 23, 59, tzinfo=BERLIN),
                course_source_repo="assignment-2-f2026",
            ),
        }
    )
    out = [f.line() for f in schedule.source_faults(s, "Course-Org")]
    assert len(out) == 1 and "assignments.assignment-2" in out[0]


def test_a_whole_repo_release_only_needs_the_repo(monkeypatch):
    # `course_source_path: /` (or `.`) means the whole repo - there is no path to look up.
    _org(monkeypatch, {"cm": ["README.md"]})
    s = Schedule(releases=[_release("everything", "/"), _release("dot", ".")])
    assert [f.line() for f in schedule.source_faults(s, "Course-Org")] == []


def test_an_unreadable_repo_is_never_reported_as_missing(monkeypatch, capsys):
    # A rate limit must not turn every source in the plan into a phantom typo.
    monkeypatch.setattr(schedule, "repo_missing", lambda org, repo: False)
    monkeypatch.setattr(schedule, "default_branch", lambda org, repo: "main")

    def boom(org, repo, branch, kind=""):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(schedule, "repo_tree", boom)
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a")])
    assert [f.line() for f in schedule.source_faults(s, "Course-Org")] == []
    # ...and it says so, so a silent tick is not mistaken for a clean one.
    assert "[skip] could not read Course-Org/cm" in capsys.readouterr().out


def test_only_a_404_says_the_repo_is_not_there(monkeypatch):
    # Absence here is an email telling faculty their materials are not in the course org,
    # hours before a lecture. The optimistic `repo_exists` reads a 403 or a 5xx as absent,
    # which is exactly the wrong answer to send somebody.
    monkeypatch.setattr(schedule, "repo_missing", lambda org, repo: False)
    monkeypatch.setattr(schedule, "default_branch", lambda org, repo: "main")

    def unreadable(org, repo, branch, kind=""):
        raise RuntimeError("HTTP 403: rate limit")

    monkeypatch.setattr(schedule, "repo_tree", unreadable)
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a", repo="gone")])
    assert schedule.source_faults(s, "Course-Org") == []
    # A positive 404, and the same plan reports the repo.
    monkeypatch.setattr(schedule, "repo_missing", lambda org, repo: True)
    (fault,) = schedule.source_faults(s, "Course-Org")
    assert fault.kind is schedule.FaultKind.MISSING_REPO
    assert fault.what == "no repo Course-Org/gone (or it is empty)"


def test_one_tree_fetch_per_repo_however_many_deploys(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(schedule, "repo_missing", lambda org, repo: False)
    monkeypatch.setattr(schedule, "default_branch", lambda org, repo: "main")

    def counting(org, repo, branch, kind=""):
        calls.append(repo)
        return ("lectures", "lectures/01_a")

    monkeypatch.setattr(schedule, "repo_tree", counting)
    s = Schedule(releases=[_release(f"lecture-{i}", "lectures/01_a") for i in range(6)])
    schedule.source_faults(s, "Course-Org")
    # ONE fetch for the whole repo: files and folders come back together, and six deploys
    # pointing into it do not become six calls (nor two, one per tree kind).
    assert calls == ["cm"]


def test_the_severity_ladder_scales_with_distance_to_the_fire_time(monkeypatch):
    # The same missing folder is a note in August and a failure the night before the
    # lecture. Distance is the whole signal - without it the check either cries wolf on
    # every term planned up front, or says nothing when it finally matters. Five rungs,
    # because how loudly it is said changes on the way down: the digest issue and a mail
    # to the people git names from a day out, the maintainer copied in the last six hours.
    now = datetime(2026, 9, 1, 12, 0, tzinfo=BERLIN)
    S = schedule.Severity

    def at(when):
        return source_fault("releases.x", fires=when).severity(now)

    assert at(now + timedelta(days=30)) is S.ADVISORY
    assert at(now + timedelta(days=2)) is S.ADVISORY
    assert at(now + timedelta(hours=25)) is S.ADVISORY
    assert at(now + timedelta(hours=23)) is S.WARNING
    assert at(now + timedelta(hours=13)) is S.WARNING
    assert at(now + timedelta(hours=11)) is S.URGENT
    assert at(now + timedelta(hours=7)) is S.URGENT
    assert at(now + timedelta(hours=5)) is S.CRITICAL
    assert at(now + timedelta(minutes=1)) is S.CRITICAL
    # At the fire time and after it: the copy did not ship. Going quiet after the fact is
    # the one behaviour that would make this check worthless.
    assert at(now) is S.MISSED
    assert at(now - timedelta(days=3)) is S.MISSED
    # Nothing pins an undated entry to a moment, so it can never escalate.
    assert at(None) is S.ADVISORY


# --------------------------------------------------------- the line of the file to edit

# One entry per shape that has to carry a line: a nested deploy, a flat assignment field,
# a comment between entries, and a release with TWO deploys - which is the case a scan of
# the text got wrong, giving the second one the first one's line.
_LOCATABLE = """\
timezone: Europe/Berlin

releases:
  # week one
  lecture_01:
    event_datetime: 2026-09-08T10:00
    deploy:
      - course_source_repo: cm
        course_source_path: lectures/01_lecture
  lecture_02:
    event_datetime: 2026-09-15T10:00
    deploy:
      - course_source_repo: cm
        course_source_path: lectures/02_lecture
      - course_source_repo: cm
        course_source_path: readings/02_readings

assignments:
  assignment-2:
    due_datetime: 2026-10-27T23:59
    course_source_repo: assignment-2-f2026
"""


def _parsed(tmp_path, text: str = _LOCATABLE) -> Schedule:
    f = tmp_path / "schedule.yml"
    f.write_text(text)
    sched, error = schedule.load_file(str(f))
    assert error is None
    return sched


def test_every_deploy_field_knows_the_line_it_is_written_on(tmp_path):
    # Per FIELD, because that is what a fault cites: `-> course_source_path` must send
    # faculty to the `course_source_path:` line, and the copy's `course_source_repo:` is
    # a different line of the same block. Captured by the loader that read the file, not
    # scanned for afterwards: a scan found the entry key and then the first matching field
    # under it, so the SECOND deploy of an entry was reported - and deep-linked - at the
    # first one's line.
    sched = _parsed(tmp_path)
    assert [
        (
            schedule.line_of(d.lines, "course_source_repo"),
            schedule.line_of(d.lines, "course_source_path"),
        )
        for r in sched.releases
        for d in r.deploy
    ] == [(8, 9), (13, 14), (15, 16)]


def test_an_assignment_field_knows_the_line_it_is_written_on(tmp_path):
    entry = _parsed(tmp_path).assignments["assignment-2"]
    assert schedule.line_of(entry.lines, "course_source_repo") == 21


def test_the_field_is_cited_wherever_it_sits_in_its_entry(tmp_path):
    # Nothing makes faculty write `course_source_repo` first, and the line to edit is the
    # line of the FIELD - not of whichever key happens to open the block.
    sched = _parsed(
        tmp_path,
        "releases:\n"
        "  lecture_01:\n"
        "    event_datetime: 2026-09-08T10:00\n"
        "    deploy:\n"
        "      - semester_dest_repo: materials\n"
        "        deploy_datetime: 2026-09-08T09:00\n"
        "        course_source_path: lectures/01\n"
        "        course_source_repo: cm\n",
    )
    (deploy,) = sched.releases[0].deploy
    assert schedule.line_of(deploy.lines, "course_source_path") == 7
    assert schedule.line_of(deploy.lines, "course_source_repo") == 8
    # A field the entry does not carry falls back to the line the entry opens on: a
    # citation pointing at the right block beats no citation, and beats a link to line 1.
    assert schedule.line_of(deploy.lines, "nonesuch") == 5


def test_a_dict_built_by_hand_has_no_line_and_says_so(tmp_path):
    # Every consumer already treats a missing line as "not known" and shows the fault
    # without a deep link, so a caller that parsed the YAML itself is not a special case.
    sched = schedule.parse(
        {
            "releases": {
                "a": {
                    "event_datetime": "2026-09-08T10:00",
                    "deploy": [{"course_source_repo": "cm", "course_source_path": "x"}],
                }
            }
        }
    )
    deploy = sched.releases[0].deploy[0]
    assert deploy.lines == {}
    assert schedule.line_of(deploy.lines, "course_source_path") is None


def test_the_line_stamp_never_reaches_the_parsed_plan(tmp_path):
    # The loader marks every mapping, and the parse takes the mark off as it consumes it.
    # Left behind it would read as an unrecognised key - or, in a label loop, as an entry.
    sched = _parsed(tmp_path)
    assert sched.dropped == []
    assert [r.label for r in sched.releases] == ["lecture_01", "lecture_02"]
    assert list(sched.assignments) == ["assignment-2"]
    assert "__lines__" not in json.dumps(asdict(sched), default=str)


def test_a_commented_out_entry_is_not_read_as_a_real_one(tmp_path):
    # The seeded schedule.yml ships its whole schema commented out, and a semester that has
    # not written a plan yet has nothing else in the file.
    sched = _parsed(
        tmp_path,
        "# releases:\n"
        "#   lecture_01:\n"
        "#     deploy:\n"
        "#       - course_source_path: lectures/01_lecture\n"
        "releases:\n"
        "  lecture_01:\n"
        "    event_datetime: 2026-09-08T10:00\n"
        "    deploy:\n"
        "      - course_source_repo: cm\n"
        "        course_source_path: lectures/01_lecture\n",
    )
    lines = sched.releases[0].deploy[0].lines
    assert (lines["course_source_repo"], lines["course_source_path"]) == (9, 10)


def test_a_fault_carries_the_line_it_is_written_on(monkeypatch, tmp_path):
    _org(monkeypatch, {"cm": ["lectures", "lectures/01_lecture"]})
    faults = {
        f.path: f for f in schedule.source_faults(_parsed(tmp_path), "Course-Org")
    }
    # The `course_source_path:` line - the line the fault names as the one to edit.
    assert faults["lectures/02_lecture"].lineno == 14
    # Two deploys under one entry are two faults at two lines, and two identities - keyed
    # on the entry alone the second inherited the first's recorded rung.
    assert faults["readings/02_readings"].lineno == 16
    assert faults["readings/02_readings"].key == (
        "releases.lecture_02[readings/02_readings].course_source_path"
    )
    # The row shape the commit comment reuses verbatim: entry, field, line, fault, when.
    assert faults["lectures/02_lecture"].line() == (
        "releases.lecture_02 -> course_source_path - schedule.yml:14 - "
        "Course-Org/cm/lectures/02_lecture does not exist - "
        "fires Tue 15 Sep 2026, 10:00 Europe/Berlin"
    )


def test_the_json_dump_is_the_plan_the_parser_understood(monkeypatch, capsys, tmp_path):
    f = tmp_path / "schedule.yml"
    f.write_text(_LOCATABLE)
    monkeypatch.setattr("sys.argv", ["schedule", "--file", str(f)])
    assert schedule.main() == 0
    dumped = json.loads(capsys.readouterr().out)
    assert dumped["timezone"] == "Europe/Berlin"
    assert dumped["releases"][0]["deploy"][0]["lines"]["course_source_path"] == 9


def test_a_deploy_datetime_dates_the_fault_not_the_class(monkeypatch):
    # The copy ships on its own clock, so that is the deadline this fault is measured to.
    _org(monkeypatch, {"cm": ["lectures"]})
    s = Schedule(
        releases=[
            Release(
                "lecture-1",
                datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                deploy=[
                    Deploy(
                        "cm",
                        "lectures/99_nope",
                        "materials",
                        None,
                        deploy_datetime=datetime(2026, 9, 1, 9, 0, tzinfo=BERLIN),
                    )
                ],
            )
        ]
    )
    assert schedule.source_faults(s, "Course-Org")[0].fires == datetime(
        2026, 9, 1, 9, 0, tzinfo=BERLIN
    )


def test_a_distant_missing_source_reports_but_keeps_the_run_green(
    monkeypatch, capsys, tmp_path
):
    # A term written up front names paths nobody has authored. That must not go red, or
    # the red X stops meaning "an entry you wrote is not in your plan".
    f = tmp_path / "schedule.yml"
    f.write_text(
        "releases:\n"
        "  lecture-1:\n"
        "    event_datetime: 2099-09-08T10:00\n"
        "    deploy:\n"
        "      - course_source_repo: cm\n"
        "        course_source_path: lectures/99_nope\n"
    )
    _org(monkeypatch, {"cm": ["lectures"]})
    monkeypatch.setattr(
        "sys.argv",
        ["schedule", "--file", str(f), "--validate", "--check-sources", "Course-Org"],
    )
    assert schedule.main() == 0
    out = capsys.readouterr().out
    assert "1 SOURCE(S) NOT IN Course-Org YET:" in out
    # The rung, and the line of the file to go and edit - the entry name alone still
    # leaves faculty scrolling a plan they wrote in August.
    assert (
        "    [advisory] releases.lecture-1 -> course_source_path - schedule.yml:6"
    ) in out
    assert "OK: nothing dropped" in out


def _imminent(tmp_path):
    f = tmp_path / "schedule.yml"
    f.write_text(
        "releases:\n"
        "  lecture-1:\n"
        "    event_datetime: 2020-09-08T10:00\n"
        "    deploy:\n"
        "      - course_source_repo: cm\n"
        "        course_source_path: lectures/99_nope\n"
    )
    return f


def test_even_a_missed_rung_source_leaves_the_parse_verdict_alone(
    monkeypatch, capsys, tmp_path
):
    # --check-sources says it never changes the exit code, and it must not: `rc` is the
    # DROPPED-ENTRY channel, which opens an issue titled "entries the scheduler cannot
    # read" and closes it on the next clean parse. A missing source routed through that
    # gets the wrong name and gets closed without ever being staged. Delivering the loud
    # rungs is the digest issue's job (source_digest), which owns a channel of its own.
    _org(monkeypatch, {"cm": ["lectures"]})
    monkeypatch.setattr(
        "sys.argv",
        [
            "schedule",
            "--file",
            str(_imminent(tmp_path)),
            "--validate",
            "--check-sources",
            "Course-Org",
        ],
    )
    assert schedule.main() == 0
    out = capsys.readouterr().out
    assert "[missed] releases.lecture-1 -> course_source_path" in out
    assert "OK: nothing dropped" in out
    # The file parses perfectly. The two verdicts stay apart.
    assert "entry/ies dropped" not in out


def test_annotations_are_emitted_by_the_process_that_knows_the_severity(
    monkeypatch, capsys, tmp_path
):
    # They used to be re-derived downstream by grepping this report for a severity prefix,
    # and the pattern silently matched only some of the rungs. Emitted here, to stderr, so
    # the human report on stdout stays clean.
    _org(monkeypatch, {"cm": ["lectures"]})
    monkeypatch.setattr(
        "sys.argv",
        [
            "schedule",
            "--file",
            str(_imminent(tmp_path)),
            "--validate",
            "--check-sources",
            "Course-Org",
            "--annotate",
        ],
    )
    assert schedule.main() == 0
    captured = capsys.readouterr()
    # `line=` is what puts the annotation on the offending line of the diff rather than at
    # the top of the file.
    assert (
        "::warning file=schedule.yml,line=6::releases.lecture-1 -> course_source_path"
    ) in captured.err
    assert "::warning" not in captured.out


def _annotated(monkeypatch, tmp_path, sched_file, output: Path | None = None) -> None:
    """Run `--validate --check-sources --annotate` against a file, optionally with a
    workflow step output to write into."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "schedule",
            "--file",
            str(sched_file),
            "--validate",
            "--check-sources",
            "Course-Org",
            "--annotate",
        ],
    )
    if output is not None:
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    else:
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert schedule.main() == 0


def test_whether_anyone_is_told_is_reported_to_the_workflow_that_asked(
    monkeypatch, capsys, tmp_path
):
    # The step after this one decides whether to comment on the push, and it used to do it
    # by grepping the report for a severity prefix - which silently matched only some of
    # the rungs. ONE boolean, written by the process that knows: a list of rung names in
    # an `if:` is a list a new rung falls out of.
    _org(monkeypatch, {"cm": ["lectures"]})
    out = tmp_path / "step_output"
    _annotated(monkeypatch, tmp_path, _imminent(tmp_path), out)
    capsys.readouterr()
    assert out.read_text().splitlines() == ["sources_notify=true"]


def test_a_plan_with_every_source_staged_says_so_rather_than_saying_nothing(
    monkeypatch, capsys, tmp_path
):
    # `false`, not an absent output: a step reading it must never have to tell "no faults"
    # from "the check did not run".
    f = tmp_path / "schedule.yml"
    f.write_text(
        "releases:\n"
        "  lecture-1:\n"
        "    event_datetime: 2099-09-08T10:00\n"
        "    deploy:\n"
        "      - course_source_repo: cm\n"
        "        course_source_path: lectures/01_a\n"
    )
    _org(monkeypatch, {"cm": ["lectures", "lectures/01_a"]})
    out = tmp_path / "step_output"
    _annotated(monkeypatch, tmp_path, f, out)
    capsys.readouterr()
    assert out.read_text().splitlines() == ["sources_notify=false"]


def test_run_by_hand_the_annotations_still_work_with_no_step_output(
    monkeypatch, capsys, tmp_path
):
    # `--annotate` has to stay usable off a runner: a maintainer checking a semester's plan
    # locally must not need to invent a GITHUB_OUTPUT for it.
    _org(monkeypatch, {"cm": ["lectures"]})
    _annotated(monkeypatch, tmp_path, _imminent(tmp_path))
    assert "::warning file=schedule.yml" in capsys.readouterr().err


def test_without_annotate_nothing_workflow_shaped_is_emitted(
    monkeypatch, capsys, tmp_path
):
    _org(monkeypatch, {"cm": ["lectures"]})
    monkeypatch.setattr(
        "sys.argv",
        [
            "schedule",
            "--file",
            str(_imminent(tmp_path)),
            "--validate",
            "--check-sources",
            "Course-Org",
        ],
    )
    schedule.main()
    captured = capsys.readouterr()
    assert "::warning" not in captured.err + captured.out


def test_worst_severity_is_the_loudest_not_the_first(monkeypatch):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=BERLIN)
    faults = [
        source_fault("a", fires=now + timedelta(days=40)),
        source_fault("b", fires=now + timedelta(hours=2)),
        source_fault("c", fires=now + timedelta(days=5)),
    ]
    assert schedule.worst_severity(faults, now) is schedule.Severity.CRITICAL
    assert schedule.worst_severity([], now) is None


def test_two_assignments_cannot_hand_out_the_same_repo():
    # A copy-paste in Maths f2026 had assignments 3 and 4 both citing assignment-2's repo.
    # Downstream nothing can tell them apart: the handout "skips" the other assignment's
    # repos and ships nothing, then the autograder re-grades the other assignment hourly
    # under this key. The second claimant is dropped, loudly, naming the first.
    meta = {
        "assignments": {
            "assignment-2": {
                "course_source_repo": "a2-f2026",
                "due_datetime": "2026-10-13",
            },
            "assignment-3": {
                "course_source_repo": "a2-f2026",
                "due_datetime": "2026-11-10",
            },
        }
    }
    sched = parse(meta)
    assert set(sched.assignments) == {"assignment-2"}
    (drop,) = [d for d in sched.dropped if "assignments.assignment-3" in d]
    assert "already used by assignments.assignment-2" in drop


def test_two_assignments_may_share_a_template_when_both_name_their_own_repos():
    # A resit off the same brief, or one template handed out to two halves of a semester.
    # Legitimate only because `semester_dest_repo` is what every semester-side artefact keys
    # on, so the two never touch each other's repos, snapshots, sheets or marks.
    meta = {
        "assignments": {
            "assignment-2": {
                "course_source_repo": "a2-f2026",
                "semester_dest_repo": "assignment-2",
                "due_datetime": "2026-10-13",
            },
            "assignment-2-resit": {
                "course_source_repo": "a2-f2026",
                "semester_dest_repo": "assignment-2-resit",
                "due_datetime": "2026-11-10",
            },
        }
    }
    sched = _parse_moved(meta)
    assert set(sched.assignments) == {"assignment-2", "assignment-2-resit"}
    assert sched.dropped == []
    assert [slug for slug, _ in schedule.entries_for_repo(sched, "a2-f2026")] == [
        "assignment-2",
        "assignment-2-resit",
    ]


def test_one_entry_leaving_the_semester_repo_to_default_re_breaks_the_pair():
    # All-or-nothing: with one of them defaulting to its slug, the two are ambiguous
    # again for every reader that starts from the template.
    meta = {
        "assignments": {
            "assignment-2": {
                "course_source_repo": "a2-f2026",
                "due_datetime": "2026-10-13",
            },
            "assignment-2-resit": {
                "course_source_repo": "a2-f2026",
                "semester_dest_repo": "assignment-2-resit",
                "due_datetime": "2026-11-10",
            },
        }
    }
    sched = _parse_moved(meta)
    assert set(sched.assignments) == {"assignment-2"}
    (drop,) = [d for d in sched.dropped if "assignments.assignment-2-resit" in d]
    assert "EVERY one of them sets its own `semester_dest_repo`" in drop


def test_resolve_target_refuses_to_choose_between_two_entries_on_one_template():
    # The refusal that makes the relaxation safe: a caller acting on ONE of them has to
    # say which, or nothing happens.
    meta = {
        "assignments": {
            "assignment-2": {
                "course_source_repo": "a2-f2026",
                "semester_dest_repo": "assignment-2",
                "due_datetime": "2026-10-13",
            },
            "assignment-2-resit": {
                "course_source_repo": "a2-f2026",
                "semester_dest_repo": "assignment-2-resit",
                "due_datetime": "2026-11-10",
            },
        }
    }
    sched = _parse_moved(meta)
    refusal = schedule.resolve_target(sched, "a2-f2026")
    assert isinstance(refusal, str)
    assert "assignment-2" in refusal and "assignment-2-resit" in refusal
    # Named, it answers with the KEY and the semester-side NAME - the only two things a
    # caller starting from a template wants, and never one standing in for the other.
    assert schedule.resolve_target(sched, "a2-f2026", "assignment-2-resit") == (
        "assignment-2-resit",
        "assignment-2-resit",
    )
    # A slug that names no entry on this template is a refusal too, not a silent fallback
    assert isinstance(schedule.resolve_target(sched, "a2-f2026", "nope"), str)
    # A template the plan does not name at all still resolves - the manual buttons work on
    # an unscheduled template - and the fallback is spelt HERE, not at three call sites.
    assert schedule.resolve_target(sched, "wk3-regression-f2026") == (
        "wk3-regression",
        "wk3-regression",
    )


def test_two_assignments_cannot_resolve_to_one_semester_name():
    # `semester_dest_repo`, else the slug, names every semester-side artefact. Two entries
    # landing on one name is the same collision as a duplicate source, one hop later:
    # the second handout finds the first's repos and skips them, and both assignments
    # then read and freeze the same snapshot. The second claimant is dropped, naming the
    # first.
    meta = {
        "assignments": {
            "assignment-1": {
                "course_source_repo": "a1-f2026",
                "due_datetime": "2026-10-13",
            },
            "assignment-1-resit": {
                "course_source_repo": "a1-resit-f2026",
                "semester_dest_repo": "assignment-1",
                "due_datetime": "2026-11-10",
            },
        }
    }
    sched = _parse_moved(meta)
    assert set(sched.assignments) == {"assignment-1"}
    (drop,) = [d for d in sched.dropped if "assignments.assignment-1-resit" in d]
    assert "semester-side name of assignments.assignment-1" in drop


def test_two_semester_dest_repos_that_match_each_other_are_refused():
    # The same collision written the other way round - neither entry uses its slug.
    meta = {
        "assignments": {
            "week-3": {
                "course_source_repo": "a3-f2026",
                "semester_dest_repo": "homework",
                "due_datetime": "2026-10-13",
            },
            "week-4": {
                "course_source_repo": "a4-f2026",
                "semester_dest_repo": "homework",
                "due_datetime": "2026-11-10",
            },
        }
    }
    sched = _parse_moved(meta)
    assert set(sched.assignments) == {"week-3"}
    assert any("semester-side name of assignments.week-3" in d for d in sched.dropped)


# ------------------------------------------- what was dropped, as the notifier sees it
#
# `dropped` is the report faculty read; `faults` is the same leftovers as ConfigFaults,
# which is what the digest issue lists and the mail names. They are built together, so the
# thing asserted here is that they cannot disagree - and that a fault knows the line.


def _from_text(text: str):
    """Parse schedule.yml TEXT with line stamps, as `load` and the validator do."""
    return schedule.parse(gh_contents.load_yaml_lines(text))


UNREADABLE = """timezone: Nowhere/Nothing
releases:
  lecture_02:
    event_datetime: not-a-date
  lecture_03:
    event_datetime: 2026-09-15T10:00
    titel: typo
assignments:
  a1:
    due_datetime: 2026-10-01
    course_source_repo: a1-f2026
    solution_datetime: 2026-09-01
"""


def test_every_dropped_line_has_a_fault_beside_it():
    sched = _from_text(UNREADABLE)
    assert len(sched.faults) == len(sched.dropped) == 4
    # Every report line ENDS with what its fault says, so the run summary and the mail
    # cannot describe the same entry differently.
    assert all(
        line.endswith(fault.what)
        for line, fault in zip(sched.dropped, sched.faults, strict=True)
    )


def test_a_dropped_entry_cites_the_line_it_is_written_on():
    faults = {f.key: f for f in _from_text(UNREADABLE).faults}
    assert faults["releases.lecture_02.event_datetime"].lineno == 4
    assert faults["releases.lecture_03.titel"].lineno == 7
    assert faults["assignments.a1.solution_datetime"].lineno == 12
    assert faults["timezone"].lineno == 1


def test_a_dropped_entry_is_an_immediate_fault():
    # No `fires`: the entry is already out of the plan, so there is no moment it is about
    # to bite at - it is at the notify bar now.
    (fault, *_) = _from_text(UNREADABLE).faults
    assert fault.fires is None
    assert fault.severity(datetime(2026, 9, 7, tzinfo=ZoneInfo("Europe/Berlin"))) is (
        schedule.Severity.WARNING
    )
    assert fault.file == "schedule.yml"
    assert fault.fix() == faults_module.FIX["schedule.yml"]


def test_a_whole_block_written_as_a_list_names_the_block():
    sched = _from_text("releases:\n  - lecture_02\n")
    (fault,) = sched.faults
    assert fault.where == "releases" and fault.field == "releases"
    assert fault.lineno == 1


def test_a_clean_plan_has_no_faults():
    sched = _from_text("releases:\n  l1:\n    event_datetime: 2026-09-15T10:00\n")
    assert sched.faults == [] and sched.dropped == []


# ------------------------------------------------------------------ archive:


def test_a_semester_that_writes_no_block_is_never_archived():
    # Archiving is opt-in: a whole org going read-only, and a site row announcing it, may
    # not happen off a date nobody typed - not even a term end.
    for meta in ({}, {"semester_end": "2026-12-18"}):
        sched = parse(meta)
        assert sched.archive is None
        assert sched.dropped == []


def test_writing_the_block_at_all_turns_archiving_on():
    # `archive:` on its own IS the switch - the date inside it is the part with a default.
    # The grace is the point: the real courses this was measured against went on being
    # pushed to for about three weeks past their last class.
    for block in (None, {}, {"show_on_site": True}):
        sched = parse({"semester_end": "2026-12-18", "archive": block})
        assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
        assert sched.archive.show_on_site is True
        assert sched.dropped == []


def test_the_block_can_say_what_the_site_says():
    # The row and the Updates box are the two places students read about the freeze, and
    # a semester that wants to say it in its own words says it once, here.
    said = "Everything here goes read-only on the 16th. Grab what you want first."
    sched = parse({"archive": {"event_datetime": "2027-02-16", "details": said}})
    assert sched.archive.details == said
    assert sched.dropped == []


def test_an_unusable_details_value_is_dropped_rather_than_printed():
    # A list reaching the deployed site as `['a', 'b']` is a hand edit that did not take -
    # flagged, never raised, and never printed. The row then reads as it does for a
    # semester that wrote no details at all.
    for said in (["a", "b"], {"text": "x"}, 7):
        sched = parse({"semester_end": "2026-12-18", "archive": {"details": said}})
        assert sched.archive.details is None
        assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
        (drop,) = sched.dropped
        assert drop.startswith("archive.details: unusable value")
        assert "no sentence at all" in drop


def test_a_blank_details_is_an_empty_slot_rather_than_a_mistake():
    # `details:` with nothing after it is how a file carries a slot for prose nobody has
    # written yet - the shape a teaching team is handed to fill in. A bare key and a `""`
    # are one intention typed two ways, so both read as no details and NEITHER is
    # reported: flagging one and not the other emailed faculty about a difference they
    # could not see in their own file.
    for said in ("", "   ", "\n"):
        for block, entry in (
            ("archive", {"details": said}),
            (
                "releases",
                {"lecture-1": {"event_datetime": "2026-09-01", "details": said}},
            ),
            ("events", {"exam": {"event_datetime": "2026-11-03", "details": said}}),
        ):
            sched = parse({block: entry})
            assert sched.dropped == [], (block, said)
            assert sched.faults == [], (block, said)


def test_a_block_with_no_details_says_nothing_about_one():
    assert parse({"archive": {"event_datetime": "2027-02-16"}}).archive.details is None


def test_a_declared_archive_date_wins_over_the_default():
    sched = parse(
        {"semester_end": "2026-12-18", "archive": {"event_datetime": "2027-01-15"}}
    )
    assert sched.archive.when == date(2027, 1, 15)


def test_a_block_with_no_term_end_and_no_date_has_no_archive_date():
    # It asked to be archived, but there is no clock to archive it against. Neither a
    # crash nor a freeze - `scheduler._no_archive_date` says so in the digest instead.
    sched = parse({"archive": {"show_on_site": False}})
    # Declared - the block is there - but with nothing to date it from.
    assert sched.archive is not None
    assert sched.archive.when is None


def test_a_semester_with_no_term_end_can_still_name_its_own_archive_date():
    assert parse({"archive": {"event_datetime": "2027-01-15"}}).archive.when == date(
        2027, 1, 15
    )


def test_show_on_site_is_only_switched_off_by_a_real_false():
    assert parse({"archive": {"show_on_site": False}}).archive.show_on_site is False
    assert parse({"archive": {}}).archive.show_on_site is True


def test_an_unreadable_show_on_site_is_flagged_and_the_row_still_shows():
    # `is not False` treated every value it could not parse as true, so a hand edit meant
    # to take the row off the site did nothing and said nothing. Flagged like the two
    # keys beside it; the row still shows, which is the default.
    for shown in ("nope", 0, [], {"when": True}):
        sched = parse(
            {"archive": {"event_datetime": "2027-02-16", "show_on_site": shown}}
        )
        assert sched.archive.show_on_site is True
        (drop,) = sched.dropped
        assert drop.startswith("archive.show_on_site: unusable value")


def test_an_unreadable_flag_is_flagged_on_every_block_that_takes_one():
    # Not the archive block alone. `show_on_site:` and `tbc:` are one key doing one thing
    # on four blocks, and three of them read an unparseable value as the default and said
    # nothing - so the one thing the key exists for silently did not happen. One helper
    # now, and a hand edit that did not take is visible wherever it was made.
    sched = parse(
        {
            "releases": {
                "lecture-1": {"event_datetime": "2026-09-01", "show_on_site": "nope"}
            },
            "assignments": {
                "a1": {
                    "course_source_repo": "a1-f2026",
                    "due_datetime": "2026-10-13",
                    "tbc": "yes",
                }
            },
            "events": {"clinic": {"event_datetime": "2026-11-10", "show_on_site": 0}},
        }
    )
    # Every one keeps its default, which is what the file said before the edit.
    assert sched.releases[0].show_on_site is True
    assert sched.assignments["a1"].tbc is False
    assert sched.events[0].show_on_site is True
    assert {drop.split(":")[0] for drop in sched.dropped} == {
        "releases.lecture-1.show_on_site",
        "assignments.a1.tbc",
        "events.clinic.show_on_site",
    }
    assert all("unusable value" in drop for drop in sched.dropped)


def test_an_unusable_details_is_flagged_on_every_block_that_takes_one():
    # The twin of the flag test above, for the other shared display key. The archive block
    # guarded this and the other three read `str(entry.get("details") or "")`, so
    # `details: ["a", "b"]` parsed clean and reached the deployed site as the literal
    # `['a', 'b']`. One key, one meaning, one guard.
    sched = parse(
        {
            "releases": {
                "lecture-1": {"event_datetime": "2026-09-01", "details": ["a", "b"]}
            },
            "assignments": {
                "a1": {
                    "course_source_repo": "a1-f2026",
                    "due_datetime": "2026-10-13",
                    "details": {"text": "x"},
                }
            },
            "events": {"clinic": {"event_datetime": "2026-11-10", "details": 7}},
            "archive": {"event_datetime": "2027-02-16", "details": ["a", "b"]},
        }
    )
    # Every row reads as it does for a semester that wrote no sentence at all.
    assert sched.releases[0].details == ""
    assert sched.assignments["a1"].details == ""
    assert sched.events[0].details == ""
    assert sched.archive.details is None
    assert {drop.split(":")[0] for drop in sched.dropped} == {
        "releases.lecture-1.details",
        "assignments.a1.details",
        "events.clinic.details",
        "archive.details",
    }
    assert all("unusable value" in drop for drop in sched.dropped)
    assert all("no sentence at all" in drop for drop in sched.dropped)


def test_an_unreadable_archive_date_falls_back_and_is_flagged():
    # A date nobody can read must not freeze the semester on a day they did not choose, and
    # must not crash the tick that reads the file either: it falls back to the default and
    # is reported through the digest issue like any other unusable value.
    sched = parse(
        {"semester_end": "2026-12-18", "archive": {"event_datetime": "16/02/2027"}}
    )
    assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
    assert len(sched.dropped) == 1
    assert sched.dropped[0].startswith("archive.event_datetime: unusable value")
    assert "freezes at its default date" in sched.dropped[0]


def test_an_archive_block_that_is_not_a_mapping_is_dropped_not_raised():
    sched = parse({"semester_end": "2026-12-18", "archive": "2027-02-16"})
    assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
    assert len(sched.dropped) == 1
    assert "not a mapping" in sched.dropped[0]
    # And it names every key that goes under it: the message is the only place a faculty
    # member reading the digest learns what the block accepts.
    for key in ("`event_datetime:`", "`title:`", "`details:`", "`show_on_site:`"):
        assert key in sched.dropped[0]


def test_a_stray_key_under_archive_is_flagged_and_ignored():
    sched = parse({"semester_end": "2026-12-18", "archive": {"when": "2027-02-16"}})
    assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
    assert len(sched.dropped) == 1
    assert "archive.when" in sched.dropped[0]


def test_archive_is_a_known_top_level_key():
    # Not in KNOWN_TOP_LEVEL, the whole block reads as a typo and every semester that writes
    # one gets a red `Validate schedule` run for a key the parser now understands.
    assert parse({"archive": {"event_datetime": "2027-02-16"}}).dropped == []


# ---------------------------------------- one word per column (title / details / type)
# The four display fields every block takes, one per column of the site's schedule table.
# Two of them are renames - `details:` was `description:`, and `archive.event_datetime:`
# was `archive.date:`. Every live semester has been migrated and the dual-key shim is gone,
# so the old spellings are now ordinary unknown keys with nothing special behind them.


def test_the_old_spelling_is_now_an_unknown_key_like_any_other_typo():
    # No special casing and no bespoke message: `description:` and `archive.date:` reach
    # `_flag_unknown_keys` exactly as `grading_dateime:` would, and the row falls back to
    # what a semester that wrote no sentence at all gets.
    sched = parse(
        {
            "semester_end": "2026-12-18",
            "releases": {
                "lecture-1": {
                    "event_datetime": "2026-09-01T10:00",
                    "title": "Probability",
                    "description": "Sample spaces.",
                }
            },
            "archive": {"date": "2027-02-16", "description": "Read-only from {date}."},
        }
    )
    assert sched.releases[0].details == ""
    assert sched.archive.details is None
    # And a stale `date:` no longer moves the freeze - the default date stands.
    assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
    assert len(sched.dropped) == 3 and len(sched.faults) == 3
    assert all("unrecognised key" in line for line in sched.dropped)
    for loc in (
        "releases.lecture-1.description",
        "archive.date",
        "archive.description",
    ):
        assert any(line.startswith(f"{loc}: ") for line in sched.dropped), loc


def test_only_the_new_spelling_is_read_where_a_file_carries_both():
    # The other direction of the same fact, so the file's meaning is pinned both ways: a
    # half-swept file reads its new keys and the line somebody forgot to delete is flagged
    # rather than quietly preferred.
    sched = parse(
        {
            "releases": {
                "lecture-1": {
                    "event_datetime": "2026-09-01T10:00",
                    "details": "The new one.",
                    "description": "The stale one.",
                }
            },
            "archive": {
                "date": "2027-02-16",
                "event_datetime": "2027-03-01",
                "details": "The new one.",
                "description": "The stale one.",
            },
        }
    )
    assert sched.releases[0].details == "The new one."
    assert sched.archive.when == date(2027, 3, 1)
    assert sched.archive.details == "The new one."
    assert len(sched.dropped) == 3
    assert all("unrecognised key" in line for line in sched.dropped)


def test_an_event_carries_its_details_and_can_be_kept_off_the_site():
    sched = parse(
        {
            "events": {
                "mid-term": {
                    "kind": "exam",
                    "title": "MidTerm",
                    "details": "Room A1. Two hours, open book.",
                    "event_datetime": "2026-11-03",
                },
                "reserve-slot": {
                    "title": "Reserve slot",
                    "event_datetime": "2026-11-10",
                    "show_on_site": False,
                },
            }
        }
    )
    exam, reserve = sched.events
    assert exam.details == "Room A1. Two hours, open book."
    assert exam.show_on_site is True
    # Kept in the plan, so faculty keep the date in the one file that holds their term.
    assert reserve.show_on_site is False
    assert sched.dropped == []


def test_an_assignment_takes_the_same_four_display_fields():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "assignment-1-f2026",
                    "due_datetime": "2026-10-13",
                    "details": "Closed form first, then gradient descent.",
                    "tbc": True,
                    "show_on_site": False,
                }
            }
        }
    )
    entry = sched.assignments["assignment-1"]
    assert entry.details == "Closed form first, then gradient descent."
    assert entry.tbc is True and entry.show_on_site is False
    assert sched.dropped == []


def test_a_tbc_assignment_moves_no_date_at_all():
    # `tbc:` is a MARK on the row. The snapshot freeze, the late window and the grading
    # cutoff all derive from `due_datetime`, and a deadline that says "(TBC)" still closes
    # exactly when it says it does.
    def parsed(tbc: bool) -> AssignmentEntry:
        return parse(
            {
                "assignments": {
                    "assignment-1": {
                        "course_source_repo": "assignment-1-f2026",
                        "handout_datetime": "2026-09-22T09:00",
                        "due_datetime": "2026-10-13",
                        "solution_datetime": "2026-10-16T09:00",
                        "tbc": tbc,
                    }
                }
            }
        ).assignments["assignment-1"]

    marked, plain = parsed(True), parsed(False)
    assert marked.tbc is True and plain.tbc is False
    for field_name in (
        "due_datetime",
        "handout_datetime",
        "solution_datetime",
    ):
        assert getattr(marked, field_name) == getattr(plain, field_name)


def test_a_release_can_declare_which_row_it_belongs_to():
    sched = parse(
        {
            "releases": {
                "week-1-clinic": {
                    "event_datetime": "2026-09-03T14:00",
                    "kind": "lab",
                    "deploy": [
                        {
                            "course_source_repo": "cm",
                            "course_source_path": "clinics/01_week-1",
                        }
                    ],
                }
            }
        }
    )
    assert sched.releases[0].kind == "lab"
    assert sched.dropped == []


def test_an_unknown_release_kind_is_flagged_and_shown_as_other():
    # Never dropped: the cost of a typo here is a row under the wrong tab, and taking a
    # whole session off the schedule instead would be far the worse of the two.
    sched = parse(
        {
            "releases": {
                "lecture-1": {"event_datetime": "2026-09-01T10:00", "kind": "lecutre"}
            }
        }
    )
    assert sched.releases[0].kind == "other"
    (drop,) = sched.dropped
    assert drop.startswith("releases.lecture-1.kind: unusable value")
    assert "shown as `other`" in drop


def test_every_content_kind_of_the_policy_is_a_release_kind():
    for kind in ("lecture", "lab", "readings", "drop-in", "exam", "other"):
        sched = parse(
            {"releases": {"x": {"event_datetime": "2026-09-01T10:00", "kind": kind}}}
        )
        assert (sched.releases[0].kind, sched.dropped) == (kind, [])


def test_display_text_on_a_readings_entry_is_its_rows_name():
    # A readings entry is a row of its own now, so its title and details show.
    display = {"title": "Week 4 readings", "details": "Two papers on attention."}
    sched = parse(
        {
            "releases": {
                "readings-4": {
                    "event_datetime": "2026-09-15T09:00",
                    "kind": "readings",
                    **display,
                }
            }
        }
    )
    assert sched.dropped == []
    (release,) = sched.releases
    assert (release.title, release.details) == (display["title"], display["details"])


def test_the_retired_spelling_on_a_readings_entry_is_reported_once_not_twice():
    # The readings check used to sweep `description` too, from when that was still an
    # accepted spelling of `details`. Now that it is an ordinary unknown key, sweeping it
    # here as well reports one line twice, under two different explanations - and the
    # readings one sends faculty to rewrite prose on another entry, where the key is just
    # as unknown.
    sched = parse(
        {
            "releases": {
                "readings-4": {
                    "event_datetime": "2026-09-15T09:00",
                    "kind": "readings",
                    "description": "Two papers on attention.",
                }
            }
        }
    )
    assert len(sched.dropped) == 1
    assert "unrecognised key" in sched.dropped[0]
    assert "claims no row of its own" not in sched.dropped[0]


def test_the_archive_row_can_be_named_and_marked_provisional():
    sched = parse(
        {
            "archive": {
                "event_datetime": "2027-02-16",
                "title": "Repositories frozen",
                "tbc": True,
            }
        }
    )
    assert sched.archive.title == "Repositories frozen"
    assert sched.archive.tbc is True
    # Display only: the day the scheduler acts on is the one that was written.
    assert sched.archive.when == date(2027, 2, 16)
    assert sched.dropped == []


def test_an_archive_block_with_no_title_carries_the_default_one():
    assert parse({"archive": {}}).archive.title == schedule.ARCHIVE_TITLE
    # And a semester that wrote no block at all has no row to name.
    assert parse({}).archive is None


def test_assignment_pages_are_numbered_as_the_site_numbers_them():
    # The ONE numbering the site names its pages by and every link to one is built from:
    # this term's templates plus the plan's entries, sorted by semester-side name, hidden
    # ones keeping their ordinal so hiding one moves nobody else's URL.
    sched = schedule.Schedule(
        assignments={
            "assignment-2": schedule.AssignmentEntry(
                course_source_repo="assignment-2-f2026",
                due_datetime=None,
                show_on_site=False,
            ),
            "project": schedule.AssignmentEntry(
                course_source_repo="assignment-4-f2026",
                due_datetime=None,
                semester_dest_repo="team-project",
            ),
        }
    )
    templates = ["assignment-1-f2026", "assignment-2-f2026", "assignment-9-s2025"]
    pages = schedule.assignment_pages("Semester-F2026", sched, templates)
    assert [(p.number, p.name, p.key) for p in pages] == [
        (1, "assignment-1", ""),  # off-plan template: a page, and no schedule key
        (2, "assignment-2", "assignment-2"),  # hidden, and still numbered
        (3, "team-project", "project"),  # the plan's own, before its template exists
    ]
    assert pages[2].stem == "03-team-project"
    # Every page's link is the semester's Join screen in the student console.
    assert pages[2].url("Semester-F2026") == policy.console_link(
        "Semester-F2026", "join"
    )


def test_pages_by_key_that_could_not_be_listed_are_none_rather_than_wrong(monkeypatch):
    def refuse(org):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(schedule, "discover_assignments", refuse)
    sched = schedule.Schedule(
        assignments={
            "a": schedule.AssignmentEntry(
                course_source_repo="a-f2026", due_datetime=None
            )
        }
    )
    assert schedule.assignment_pages_by_key("Course", "Semester-f2026", sched) == {}


# ------------------------------------------------ the solution notice (--previous)

_WITH_SOLUTION = """\
assignments:
  a1:
    course_source_repo: t1
    handout_datetime: 2026-09-22T09:00
    due_datetime: 2026-10-13
    solution_datetime: 2026-10-16T09:00
  a2:
    course_source_repo: t2
    handout_datetime: 2026-09-22T09:00
    due_datetime: 2026-10-20
    solution_datetime: 2026-10-23T09:00
"""


def test_only_an_entry_that_has_just_gained_a_solution_date_is_noticed():
    after = parse(yaml.safe_load(_WITH_SOLUTION))
    before = yaml.safe_load(
        _WITH_SOLUTION.replace("    solution_datetime: 2026-10-16T09:00\n", "")
    )
    (note,) = schedule.solution_notices(before, after)
    assert note.where == "assignments.a1" and note.field == "solution_datetime"
    assert course.SOLUTION_WARNING in note.what
    assert schedule.solution_notices(yaml.safe_load(_WITH_SOLUTION), after) == []


def test_an_entry_the_old_file_could_not_run_is_not_noticed_as_new():
    # Two entries on one template, their repo names in an assignments.yml the old file is
    # read without: parsed, the second is dropped. Read as written, it already carried
    # its solution date, so nothing is new.
    shared = _WITH_SOLUTION.replace("course_source_repo: t2", "course_source_repo: t1")
    before = yaml.safe_load(shared)
    assert (
        schedule.solution_notices(before, parse(yaml.safe_load(_WITH_SOLUTION))) == []
    )


def _validate(monkeypatch, tmp_path, capsys, previous: str | None) -> str:
    now = tmp_path / "schedule.yml"
    now.write_text(_WITH_SOLUTION)
    argv = ["schedule", "--file", str(now), "--validate"]
    if previous is not None:
        (tmp_path / "before.yml").write_text(previous)
        argv += ["--previous", str(tmp_path / "before.yml")]
    monkeypatch.setattr("sys.argv", argv)
    assert schedule.main() == 0  # a notice never changes the verdict
    return capsys.readouterr().err


def test_validate_notices_each_new_solution_date_on_its_line(
    monkeypatch, tmp_path, capsys
):
    err = _validate(monkeypatch, tmp_path, capsys, "assignments: {}\n")
    notices = [ln for ln in err.splitlines() if ln.startswith("::notice")]
    assert len(notices) == 2
    assert notices[0].startswith("::notice file=schedule.yml,line=6::")


@pytest.mark.parametrize("previous", [None, "assignments: [\n"])
def test_no_previous_file_or_an_unreadable_one_gives_no_notice(
    monkeypatch, tmp_path, capsys, previous
):
    assert "::notice" not in _validate(monkeypatch, tmp_path, capsys, previous)
