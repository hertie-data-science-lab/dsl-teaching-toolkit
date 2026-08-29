"""dsl_course.schedule pure core - classroom-config/schedule.yml is the single home for a
cohort's release plan (releases), due dates (assignments), and display-only calendar rows
(events); a wrong parse here silently mis-times a release or mis-pins a grading deadline,
so it's the bit that must be right. Times are timezone-aware (naive -> Europe/Berlin by
default).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dsl_course import course, schedule
from dsl_course.schedule import (
    AssignmentEntry,
    Deploy,
    Event,
    Release,
    Schedule,
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


def test_coerce_datetime_naive_gets_the_cohort_tz_and_an_offset_is_converted_to_it():
    naive = _coerce_datetime("2026-09-15T14:00", BERLIN)
    assert naive.tzinfo is not None
    assert naive.utcoffset() == BERLIN.utcoffset(naive.replace(tzinfo=None))
    # An explicit offset names an INSTANT; it is honoured as that instant, but stored in
    # the cohort's own clock - 14:00 UTC is 16:00 in Berlin in September. Every consumer
    # then reads a cohort wall-clock time without re-deriving the zone (the site used to
    # convert at print time, and anything that forgot printed the wrong hour).
    aware = _coerce_datetime("2026-09-15T14:00+00:00", BERLIN)
    assert aware == datetime(2026, 9, 15, 14, 0, tzinfo=ZoneInfo("UTC"))  # same instant
    assert aware.tzinfo is BERLIN and (aware.hour, aware.minute) == (16, 0)


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
                        "cohort_dest_repo": "materials",
                        "cohort_dest_path": "lectures/02_intro",
                    }
                ],
            },
            "a1-handout": {
                "event_datetime": "2026-10-15T00:00",
                "assignment": "assignment-1-f2026",
            },
        },
        "assignments": {
            "assignment-1": {
                "course_source_repo": "a-f2026",
                "due_datetime": "2026-10-13",
                "grading_datetime": "2026-10-15",
            }
        },
        "events": {
            "final": {"type": "exam", "title": "Final", "event_datetime": "2026-12-15"},
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
    assert sched.releases[1].assignment == "assignment-1-f2026"
    assert (
        sched.assignments["assignment-1"]
        .due_datetime.isoformat()
        .startswith("2026-10-13T23:59:59")
    )
    assert (
        sched.assignments["assignment-1"]
        .grading_datetime.isoformat()
        .startswith("2026-10-15")
    )
    # events are display-only rows, in calendar order; `type` defaults to special_event
    assert sched.events == [
        Event(
            label="project-clinic",
            title="Project Clinic",
            when=datetime(2026, 10, 14, 10, 0, tzinfo=BERLIN),
            type="special_event",
        ),
        Event(label="final", title="Final", when=date(2026, 12, 15), type="exam"),
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


def test_deploy_accepts_single_mapping_defaults_cohort_dest_path_none():
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
    # The org prefixes are a hard rename with no alias handling, so a cohort whose
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
                "mid-term": {"type": "exam", "event_datetime": "2026-11-03"},
                "final": {"type": "exam", "event_datetime": "2026-12-15T14:00"},
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


def test_event_explicit_offset_keeps_its_instant_and_is_stored_in_the_cohort_tz():
    sched = parse(
        {
            "timezone": "Europe/Berlin",
            "events": {"remote": {"event_datetime": "2026-12-15T14:00+00:00"}},
        }
    )
    when = sched.events[0].when
    assert when == datetime(2026, 12, 15, 14, 0, tzinfo=ZoneInfo("UTC"))  # same instant
    assert when.hour == 15 and when.tzinfo == BERLIN  # ...on the cohort's clock (CET)


def test_event_timezone_comes_from_the_cohort_setting():
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
            "mid-term": {"type": "Exam", "event_datetime": "2026-11-03"},
            "clinic": {"event_datetime": "2026-10-14T10:00"},
            "typo": {"type": "examm", "event_datetime": "2026-10-20"},
        }
    }
    events = {e.label: e for e in parse(meta).events}
    assert events["mid-term"].type == "exam"  # case-normalised
    assert events["clinic"].type == "special_event"
    assert (
        events["typo"].type == "special_event"
    )  # unknown value -> the display default


def test_events_sort_by_date_with_undated_last():
    meta = {
        "events": {
            "resit": {"event_datetime": "tbc"},
            "final": {"type": "exam", "event_datetime": "2026-12-15T14:00"},
            "clinic": {"event_datetime": date(2026, 10, 14)},
        }
    }
    # whole-day and timed entries sort against each other; TBC rows go to the end
    assert [e.label for e in parse(meta).events] == ["clinic", "final", "resit"]


def test_tbc_semantics_for_events():
    meta = {
        "events": {
            "mid-term": {"type": "exam", "event_datetime": "2026-11-03", "tbc": True},
            "resit": {"type": "exam", "event_datetime": "tbc"},
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
    assert (
        parse({"assignments": {"assignment-1": {"max_team_size": 2}}}).assignments == {}
    )


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


def test_max_team_size_parses_and_defaults_to_none():
    meta = {
        "assignments": {
            "assignment-4-project": {
                "course_source_repo": "a-f2026-1",
                "due_datetime": "2026-11-15",
                "max_team_size": 3,
            },
            "assignment-1": {
                "course_source_repo": "a-f2026-2",
                "due_datetime": "2026-10-13",
            },
            "bad": {
                "course_source_repo": "a-f2026-3",
                "due_datetime": "2026-10-20",
                "max_team_size": "lots",
            },
        }
    }
    entries = parse(meta).assignments
    assert entries["assignment-4-project"].max_team_size == 3
    assert entries["assignment-1"].max_team_size is None
    assert entries["bad"].max_team_size is None  # malformed -> unset, and flagged


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


def test_assignment_type_parses_and_rejects_unknown_values():
    meta = {
        "assignments": {
            "assignment-4-project": {
                "course_source_repo": "a-f2026-1",
                "due_datetime": "2026-11-15",
                "type": "group",
            },
            "assignment-1": {
                "course_source_repo": "a-f2026-2",
                "due_datetime": "2026-10-13",
                "type": "Individual",
            },
            "assignment-2": {
                "course_source_repo": "a-f2026-3",
                "due_datetime": "2026-10-27",
            },
            "typo": {
                "course_source_repo": "a-f2026-4",
                "due_datetime": "2026-11-01",
                "type": "grp",
            },
        }
    }
    sched = parse(meta)
    entries = sched.assignments
    assert entries["assignment-4-project"].type == "group"
    assert entries["assignment-1"].type == "individual"  # case-normalised
    assert entries["assignment-2"].type is None
    # unknown value still falls back to individual, but is now surfaced (a group
    # assignment typo'd here would otherwise silently get one repo per student)
    assert entries["typo"].type is None
    assert any("assignments.typo.type" in d and "grp" in d for d in sched.dropped)


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
    monkeypatch.setattr(S, "get_file_content", lambda org, repo, path: store["text"])
    writes = []
    monkeypatch.setattr(
        "dsl_course.schedule.put_file",
        lambda org, repo, path, content, msg: writes.append(content.decode()) or True,
    )
    S.record_handout("Cohort-f2026", "assignment-1", "2026-09-22T14:05")
    (new,) = writes
    sched = S.parse(yaml.safe_load(new))
    assert (
        sched.assignments["assignment-1"]
        .handout_datetime.isoformat()
        .startswith("2026-09-22T14:05")
    )
    # second call sees the recorded value and is a no-op
    store["text"] = new
    S.record_handout("Cohort-f2026", "assignment-1", "2026-09-23T09:00")
    assert len(writes) == 1


def test_the_plan_is_read_once_per_cohort_and_a_handout_reopens_it(monkeypatch):
    # An hourly tick reads the plan in the scheduler and again inside every handout and
    # collection it fires - one GET each, for a file that only a person or record_handout
    # changes. record_handout IS that writer, so it drops the memo.
    reads: list[str] = []
    monkeypatch.setattr(
        schedule,
        "get_file_content",
        lambda org, repo, path: reads.append(org) or "timezone: Europe/Berlin\n",
    )
    schedule.load("Cohort-f2026")
    schedule.load("Cohort-f2026")
    assert reads == ["Cohort-f2026"], "the plan was re-read within one run"

    schedule.load("Cohort-f2027")
    assert len(reads) == 2, "one cohort's plan answered for another"

    monkeypatch.setattr(schedule, "put_file", lambda *a, **k: True)
    schedule.record_handout("Cohort-f2026", "assignment-1", "2026-09-22T14:05")
    schedule.load("Cohort-f2026")
    assert len(reads) == 3, "the memo survived a write to schedule.yml"


# --------------------------------------------------------- a file that does not parse
#
# The incident: a faculty member left an unclosed flow mapping in schedule.yml, so
# `yaml.safe_load` raised inside `schedule.load` and took down BOTH the hourly Scheduled
# release run AND Sync site for that cohort - the site kept showing the template's
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

    sched = S.load("Cohort-f2026")

    # same shape a missing schedule.yml yields - nothing scheduled, nothing raised - but
    # flagged, so the hourly scheduler can fail its run instead of ticking green for ever
    assert sched == Schedule(unparseable=True)
    err = capsys.readouterr().err
    # self-diagnosing: which cohort, which file, the parser's own line/column, what to do
    assert "Cohort-f2026/classroom-config/schedule.yml is NOT valid YAML" in err
    assert "line 5" in err and "flow mapping" in err
    assert "fix classroom-config/schedule.yml on main" in err
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

    sched = S.load("Cohort-f2026")

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
    assert S.load("Cohort-f2026") == Schedule(unparseable=True)
    assert "not a mapping" in capsys.readouterr().err


def test_a_comment_only_schedule_is_empty_not_unparseable(monkeypatch, capsys):
    # `yaml.safe_load` returns None for a file of nothing but comments. That is an empty
    # plan, exactly like an absent file - not a fault to redden the hourly cron with.
    from dsl_course import schedule as S

    monkeypatch.setattr(
        S, "get_file_content", lambda org, repo, path: "# nothing yet\n"
    )
    assert S.load("Cohort-f2026") == Schedule()
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
            "events": {"mid-term": {"type": "exam"}},
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
    # each line names the field at fault AND what the cohort loses by it
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


def test_cohort_dest_repo_parses_and_defaults_to_the_slug():
    from dsl_course.schedule import cohort_name

    sched = parse(
        {
            "assignments": {
                "hw": {"course_source_repo": "a-f2026-1", "due_datetime": "2026-10-13"},
                "named": {
                    "course_source_repo": "a-f2026-2",
                    "cohort_dest_repo": "homework-1",
                    "due_datetime": "2026-10-20",
                },
                "blank": {
                    "course_source_repo": "a-f2026-3",
                    "cohort_dest_repo": "  ",
                    "due_datetime": "2026-10-27",
                },
            }
        }
    )
    # unset (and blank) -> the slug IS the cohort-side name; set -> it wins
    assert cohort_name("hw", sched.assignments["hw"]) == "hw"
    assert cohort_name("named", sched.assignments["named"]) == "homework-1"
    assert cohort_name("blank", sched.assignments["blank"]) == "blank"


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
    # session row). Both demo cohorts carried three of these on live sessions, each looking
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

    sched = S.load("Cohort-f2026")

    assert sched.assignments == {}
    err = capsys.readouterr().err
    # which cohort, which file, which entry, which field, and what it costs
    assert "Cohort-f2026/classroom-config/schedule.yml" in err
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
        "example-course/cohort-org/schedule.yml",
        "templates/classroom-config/schedule.yml",
    ],
)
def test_shipped_schedules_parse_with_nothing_dropped(path):
    # The CI gate. The example is what faculty copy and the template is what every new
    # cohort is seeded with, so either one silently dropping an entry would teach the
    # mistake rather than catch it.
    full = Path(__file__).resolve().parents[1] / path
    sched, error = schedule.load_file(str(full))
    assert error is None, error
    assert sched.dropped == [], f"{path} drops entries:\n" + "\n".join(sched.dropped)


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
    sched = S.load("Cohort-f2026")  # must not raise
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


def test_an_unparseable_grading_datetime_is_flagged_not_silently_the_due_date():
    sched = parse(
        {
            "assignments": {
                "a1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "grading_datetime": "next tuesday",
                }
            }
        }
    )
    # the documented fallback still applies - grading pins to the due date
    assert (
        schedule.grading_datetime_at(sched, "a1")
        == sched.assignments["a1"].due_datetime
    )
    (line,) = sched.dropped
    assert line.startswith("assignments.a1.grading_datetime:")
    assert "falls back to the due date" in line


def test_a_non_integer_max_team_size_is_flagged():
    sched = parse(
        {
            "assignments": {
                "project": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-11-15",
                    "type": "group",
                    "max_team_size": "lots",
                }
            }
        }
    )
    assert sched.assignments["project"].max_team_size is None
    (line,) = sched.dropped
    assert line.startswith("assignments.project.max_team_size:")
    assert "'lots'" in line and "Join team" in line


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
        {"events": {"mid-term": {"type": "exma", "event_datetime": "2026-11-03"}}}
    )
    assert sched.events[0].type == "special_event"  # the row still shows
    (line,) = sched.dropped
    assert line.startswith("events.mid-term.type:")
    assert "'exma'" in line and "not an exam" in line


def test_an_absent_optional_value_is_never_flagged():
    # Omitting handout/grading/deploy/type/max_team_size is the documented way to take
    # their defaults - only a value that IS there and cannot be read is a fault.
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
    monkeypatch.setattr(S, "get_file_content", lambda org, repo, path: flow)
    monkeypatch.setattr(
        "dsl_course.schedule.put_file",
        lambda *a, **k: pytest.fail("must not write into a shape it cannot parse"),
    )

    S.record_handout("Cohort-f2026", "assignment-1", "2026-09-22T14:05")

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
    monkeypatch.setattr(S, "get_file_content", lambda org, repo, path: good)
    monkeypatch.setattr("dsl_course.schedule.put_file", lambda *a, **k: False)

    S.record_handout("Cohort-f2026", "assignment-1", "2026-09-22T14:05")

    err = capsys.readouterr().err
    assert "could NOT record the assignment-1 handout" in err
    assert "2026-09-22T14:05" in err
    assert "on record nowhere" in err


def test_validate_cli_reports_an_unreadable_cohort_schedule(monkeypatch, capsys):
    # An absent schedule.yml is an empty Schedule (valid: nothing planned yet), but a read
    # that failed outright now raises - the CLI turns that into a line and a red run,
    # rather than a traceback or a false "OK: nothing dropped".
    def boom(cohort_org):
        raise RuntimeError("could not read Cohort-f2026/classroom-config/schedule.yml")

    monkeypatch.setattr(schedule, "load", boom)
    monkeypatch.setattr(
        "sys.argv", ["schedule", "--cohort-org", "Cohort-f2026", "--validate"]
    )
    assert schedule.main() == 1
    assert "could not read" in capsys.readouterr().err


# --------------------------------------------------------- source existence (advisory)


def _org(monkeypatch, trees: dict[str, list[str]]):
    """Fake a course org as {repo: [every path in it]}. A repo absent from `trees` does not
    exist; one mapped to [] exists but is empty."""
    monkeypatch.setattr(schedule, "repo_exists", lambda org, repo: repo in trees)
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
    assert out[0].startswith("releases.lecture-2 -> course_source_path (due ")
    assert "`cm/lectures/02_b` does not exist yet" in out[0]


def test_missing_sources_reports_a_repo_that_is_not_there_at_all(monkeypatch):
    _org(monkeypatch, {})
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a", repo="typo-repo")])
    out = [f.line() for f in schedule.source_faults(s, "Course-Org")]
    assert len(out) == 1 and "no repo `Course-Org/typo-repo`" in out[0]


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


def test_an_unreadable_repo_is_never_reported_as_missing(monkeypatch):
    # A rate limit must not turn every source in the plan into a phantom typo.
    monkeypatch.setattr(schedule, "repo_exists", lambda org, repo: True)
    monkeypatch.setattr(schedule, "default_branch", lambda org, repo: "main")

    def boom(org, repo, branch, kind=""):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(schedule, "repo_tree", boom)
    s = Schedule(releases=[_release("lecture-1", "lectures/01_a")])
    assert [f.line() for f in schedule.source_faults(s, "Course-Org")] == []


def test_one_tree_fetch_per_repo_however_many_deploys(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(schedule, "repo_exists", lambda org, repo: True)
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
    # every term planned up front, or says nothing when it finally matters.
    now = datetime(2026, 9, 1, 12, 0, tzinfo=BERLIN)
    S = schedule.Severity

    def at(when):
        return schedule.SourceFault("releases.x", "gone", when, "f").severity(now)

    assert at(now + timedelta(days=30)) is S.ADVISORY
    assert at(now + timedelta(days=8)) is S.ADVISORY
    assert at(now + timedelta(days=6)) is S.WARNING
    assert at(now + timedelta(hours=49)) is S.WARNING
    assert at(now + timedelta(hours=47)) is S.ERROR
    # Already passed: the copy did not ship. Going quiet after the fact is the one
    # behaviour that would make this check worthless.
    assert at(now - timedelta(days=3)) is S.ERROR
    # Nothing pins an undated entry to a moment, so it can never escalate.
    assert at(None) is S.ADVISORY


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
    # `!` (not `!!`, and not the `-` a drop uses): the workflow greps these prefixes to
    # pick ::warning:: over ::error::, so conflating them would mis-rank every fault.
    assert "    [advisory] releases.lecture-1 -> course_source_path" in out
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


def test_even_an_error_rung_source_leaves_the_parse_verdict_alone(
    monkeypatch, capsys, tmp_path
):
    # --check-sources says it never changes the exit code, and it must not: `rc` is the
    # DROPPED-ENTRY channel, which opens an issue titled "entries the scheduler cannot
    # read" and closes it on the next clean parse. A missing source routed through that
    # gets the wrong name and gets closed without ever being staged. Escalating the error
    # rung is the hourly pre-flight's job (scheduler._preflight_sources), which owns a
    # channel of its own.
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
    assert "[error] releases.lecture-1 -> course_source_path" in out
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
    assert "::warning file=schedule.yml::releases.lecture-1 -> course_source_path" in (
        captured.err
    )
    assert "::warning" not in captured.out


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
        schedule.SourceFault("a", "gone", now + timedelta(days=40), "f"),
        schedule.SourceFault("b", "gone", now + timedelta(hours=2), "f"),
        schedule.SourceFault("c", "gone", now + timedelta(days=5), "f"),
    ]
    assert schedule.worst_severity(faults, now) is schedule.Severity.ERROR
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
