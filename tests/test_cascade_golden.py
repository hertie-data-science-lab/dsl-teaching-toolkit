"""Golden vectors pinning the console's cascade (`console/src/model/cascade.ts`) to
`settings.resolve` before the console stops recomputing it (decision 0039 rule 2).

Each vector is a raw layer stack and the key asked; the fixture carries what the engine
answers. Rewrite the fixture with `DSL_WRITE_GOLDEN=1`."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dsl_course import settings

FIXTURE = Path(__file__).parents[1] / "console/test/fixtures/cascade-golden.json"

_STACKS = [
    {},
    {"course": {"max_team_size": 4}},
    {"course": {"max_team_size": 4}, "semester": {"max_team_size": 3}},
    {"semester": {"max_team_size": 3}, "entry": {"max_team_size": 2}},
    # The late pair: a layer naming one half answers the other with none.
    {
        "course": {"late_window_days": 7, "late_penalty_per_day": "5%"},
        "semester": {"late_window_days": 3},
    },
    {"entry": {"late_penalty_per_day": "2%"}, "semester": {"late_window_days": 4}},
    # Refused values fall through to the next layer.
    {
        "semester": {
            "max_team_size": -3,
            "visibility": "secret",
            "team_formation": "assigned",
        }
    },
    {"semester": {"late_window_days": -3, "late_penalty_per_day": "5%"}},
    {"course": {"max_team_size": "4", "visibility": "Private"}},
    {
        "entry": {"team_formation": " Assigned "},
        "course": {"team_formation": "self_select"},
    },
    # Blank values state nothing.
    {
        "entry": {"max_team_size": "", "visibility": None},
        "course": {"max_team_size": 6},
    },
    {"entry": {"submit_url": "https://moodle.example.org/CHANGE-ME"}},
    {"entry": {"submit_url": "https://moodle.example.org/a/1"}},
    {"semester": {"marks_per_question": "no"}, "course": {"marks_per_question": True}},
    # team_formation stated nowhere: the engine's fallback, from the institution layer.
    {"course": {"max_team_size": 2}},
]


def _read(layer: str, raw: dict) -> dict:
    """One raw layer as the engine reads it (the files spell `visibility` long)."""
    spelt = {settings.FILE_NAMES.get(k, k): v for k, v in raw.items()}
    if layer == "entry":
        return settings._usable(settings.inline_settings(spelt, "entry", []))
    return settings._usable(settings.read_defaults(spelt, layer, []))


def vectors() -> dict:
    institution = settings.institution_defaults()
    out = []
    for raw in _STACKS:
        stack = [(s, _read(s, raw.get(s, {}))) for s in ("entry", "semester", "course")]
        stack.append(("institution", institution))
        for key in settings.RUN_KEYS:
            value, source = settings.resolve(key, stack)
            out.append({"layers": raw, "key": key, "value": value, "source": source})
    return {"institution": institution, "vectors": out}


def test_the_golden_vectors_are_what_the_engine_answers():
    want = json.dumps(vectors(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("DSL_WRITE_GOLDEN"):
        FIXTURE.write_text(want)
    assert FIXTURE.read_text() == want
