"""A deliberately small JSON Schema subset: type, enum, pattern, properties, required,
additionalProperties, items. Enough for the schemas this package writes (the console
request, the operation arguments, the institution policy), and no new runtime dependency
on every runner.
"""

from __future__ import annotations

import re

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


def _is(value: object, kind: str) -> bool:
    # bool is an int in Python and never an integer in JSON Schema.
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[kind])


def _js_anchors(pattern: str) -> str:
    """`pattern` as JSON Schema (ECMA) reads it: a closing `$` is the end of the string.
    Python's `$` also matches before a final newline, so `alice\\n` would pass as a handle."""
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


def validate(value: object, schema: dict, where: str = "$") -> list[str]:
    """Every way `value` breaks `schema`, as `path: problem` lines. Empty means valid.

    The lines name the path and the rule, never the value: a request can carry a handle."""
    problems: list[str] = []
    kind = schema.get("type")
    if kind:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is(value, k) for k in kinds):
            return [f"{where}: must be {' or '.join(kinds)}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(
            f"{where}: must be one of {', '.join(map(str, schema['enum']))}"
        )
    if (
        "pattern" in schema
        and isinstance(value, str)
        and not re.search(_js_anchors(schema["pattern"]), value)
    ):
        problems.append(f"{where}: does not match the expected form")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{where}.{key}: is required")
        for key, item in value.items():
            if key in props:
                problems += validate(item, props[key], f"{where}.{key}")
            elif schema.get("additionalProperties") is False:
                problems.append(f"{where}.{key}: is not a known field")
            elif isinstance(schema.get("additionalProperties"), dict):
                problems += validate(
                    item, schema["additionalProperties"], f"{where}.{key}"
                )
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += validate(item, schema["items"], f"{where}[{i}]")
    return problems
