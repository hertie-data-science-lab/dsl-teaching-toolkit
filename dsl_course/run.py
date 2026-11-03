"""The per-run context: what one CLI invocation reads once and keeps until it writes.

A process is one run (a CLI, or the Console running one in-process), so there is one
`Run` at a time, `current()`. It holds:

- every per-run memo (`per_run`): a file's text, a repo's tree, a course's defaults, the
  parsed schedule, the login the token belongs to, ... Each answer names what it was read
  from (`Scope`), and a write this process makes there forgets it - `ghcli` tells
  `forget` about every write, as it tells `gh_contents`' read memo;
- `listings`: the scheduler tick's one listing of a semester org, which the handouts of
  that tick write back into (`discovery.listing_row`) and which no write forgets.

`reset` puts the process back to the state it starts in: a fresh `Run`, and every module's
read-once switch back off (`on_reset`). Nothing in a CLI calls it; `tests/conftest.py`
calls it between tests.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

# What an answer was read from, casefolded where GitHub is case-insensitive: `(org,)` for
# something about the org as a whole (its listing), `(org, repo)` for a repo's files or
# history, `(org, repo, path)` for one file. None for an answer no write can change (the
# token's login, whether a central ref exists, a probe of this machine).
Scope = tuple[str, ...]
_Key = tuple[tuple, tuple]


class Run:
    """One CLI invocation's state. See the module docstring."""

    def __init__(self) -> None:
        # The scheduler tick's listing per casefolded semester org: None is "could not
        # look", which each pass reads its own way; an org with no entry has no tick.
        self.listings: dict[str, dict[str, dict] | None] = {}
        self._memos: dict[str, dict[_Key, tuple[Scope | None, Any]]] = {}

    def held(self, name: str) -> dict[_Key, tuple[Scope | None, Any]]:
        return self._memos.setdefault(name, {})

    def holds(self, fn: Callable) -> int:
        """How many answers `fn` (a `per_run` function) holds in this run."""
        return len(self._memos.get(fn.run_name, {}))

    def drop(self, name: str) -> None:
        self._memos.pop(name, None)

    def forget(self, stale: Callable[[Scope], bool]) -> None:
        """Drop every answer whose scope `stale` says a write may have changed."""
        for held in self._memos.values():
            for key in [k for k, (scope, _) in held.items() if scope and stale(scope)]:
                del held[key]

    def listing(self, org: str) -> dict[str, dict] | None:
        """This tick's listing of `org` (see `listings`)."""
        return self.listings.get(org.casefold())


_current = Run()
_resets: list[Callable[[], None]] = []


def current() -> Run:
    return _current


def on_reset(hook: Callable[[], None]) -> None:
    """Run `hook()` on every `reset`: a module's own read-once switch going back off."""
    _resets.append(hook)


def reset() -> None:
    """A fresh `Run`, and every read-once memo back off, as at process start."""
    global _current
    _current = Run()
    for hook in _resets:
        hook()


def scope(org: str, repo: str = "", path: str = "") -> Scope:
    """A `Scope`: the owner and repo casefolded (GitHub's names are not case-sensitive),
    the path inside the repo as written."""
    if not repo:
        return (org.casefold(),)
    if not path:
        return (org.casefold(), repo.casefold())
    return (org.casefold(), repo.casefold(), path)


def per_run(read_from: Callable[..., Scope] | None = None):
    """Memoise a function in the current `Run`, keyed by its arguments, as `functools.cache`
    does: an exception is not kept, and every caller gets the same object. `read_from`,
    called with the same arguments, says what the answer was read from, so a write there
    forgets it; without one only `reset` does. `fn.cache_clear()` forgets every answer the
    function holds in this run."""

    def wrap(fn: Callable) -> Callable:
        # The id as well: two closures of one name are two functions.
        name = f"{fn.__module__}.{fn.__qualname__}@{id(fn):x}"

        @functools.wraps(fn)
        def memoised(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            held = _current.held(name)
            if key in held:
                return held[key][1]
            value = fn(*args, **kwargs)
            where = read_from(*args, **kwargs) if read_from else None
            _current.held(name)[key] = (where, value)
            return value

        memoised.run_name = name
        memoised.cache_clear = lambda: _current.drop(name)
        return memoised

    return wrap
