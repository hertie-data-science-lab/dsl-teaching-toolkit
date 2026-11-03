"""The per-run context: a `per_run` answer is kept for the run, and a write this process
makes forgets exactly the answers it may have changed (`ghcli._wrote`)."""

from __future__ import annotations

import pytest

from dsl_course import ghcli, run


def _counter(read_from=None):
    calls = []

    @run.per_run(read_from)
    def read(*args):
        calls.append(args)
        return len(calls)

    return read, calls


def test_an_answer_is_read_once_per_run_and_reset_forgets_it():
    read, _calls = _counter()
    assert read("a") == read("a") == 1
    assert read("b") == 2
    run.reset()
    assert read("a") == 3


def test_an_exception_is_not_kept():
    attempts = []

    @run.per_run()
    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("rate limited")
        return "ok"

    with pytest.raises(RuntimeError):
        flaky()
    assert flaky() == flaky() == "ok"
    assert len(attempts) == 2


@pytest.mark.parametrize(
    ("kind", "targets", "forgotten"),
    [
        # One file committed: that file's answer and the repo's, not a sibling file's.
        (ghcli.FILE, {"org/semester-config/schedule.yml"}, {"file", "repo", "org"}),
        (ghcli.FILE, {"org/semester-config/students.csv"}, {"repo", "org"}),
        # A push to the repo: everything read from it.
        (ghcli.FILES, {"org/semester-config"}, {"file", "repo", "org"}),
        # A repo made or deleted in the org: everything read from the org.
        (ghcli.FILES, {"org"}, {"file", "repo", "org"}),
        # A push elsewhere changes nothing held here.
        (ghcli.FILES, {"other/semester-config"}, set()),
        (ghcli.FILES, {"org/materials"}, {"org"}),
        # A settings write changes the org's listing only.
        (ghcli.META, {"org/semester-config"}, {"org"}),
        (ghcli.ISSUES, {"org/semester-config"}, set()),
        (ghcli.ALL, {ghcli.EVERYTHING}, {"file", "repo", "org"}),
    ],
)
def test_a_write_forgets_what_it_may_have_changed(kind, targets, forgotten):
    reads: dict[str, list] = {"file": [], "repo": [], "org": [], "process": []}
    scopes = {
        "file": ("Org", "Semester-Config", "schedule.yml"),
        "repo": ("Org", "semester-config"),
        "org": ("ORG",),
        "process": None,
    }
    readers = {}
    for name, where in scopes.items():

        def read(name=name):
            reads[name].append(1)
            return name

        readers[name] = run.per_run((lambda w=where: run.scope(*w)) if where else None)(
            read
        )
    for reader in readers.values():
        reader()
    ghcli._wrote(kind, frozenset(targets))
    for reader in readers.values():
        reader()
    assert {name for name, n in reads.items() if len(n) == 2} == forgotten


def test_cache_clear_forgets_one_function_only():
    one, one_calls = _counter()
    two, two_calls = _counter()
    one(), two()
    one.cache_clear()
    one(), two()
    assert (len(one_calls), len(two_calls)) == (2, 1)
    assert run.current().holds(one) == 1
