"""The shape of the package itself: every module imports, nothing imports inside a
function body, and the dependency graph has no cycles.

These are the invariants the module split bought. A function-local `from .x import y` is
almost always a cycle someone worked around instead of fixing, and it hides a dependency
the import graph is supposed to show.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import dsl_course

PACKAGE = Path(dsl_course.__file__).parent
MODULES = sorted(m.name for m in pkgutil.iter_modules([str(PACKAGE)]))


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports(name):
    importlib.import_module(f"dsl_course.{name}")


def _function_local_imports(tree: ast.AST) -> list[str]:
    """Every `import`/`from ... import` that sits inside a function body.

    Absolute ones count too. The rule was written for the relative kind, where a
    function-local import is nearly always a worked-around cycle - but `from pathlib
    import Path` buried in one function is the same hidden dependency and the same
    per-call lookup, and it sat there unflagged because the check only asked about
    `child.level`."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom):
                found.append(
                    f"{node.name}: from {'.' * child.level}{child.module or ''}"
                )
            elif isinstance(child, ast.Import):
                found.append(f"{node.name}: import {child.names[0].name}")
    return found


@pytest.mark.parametrize("name", MODULES)
def test_no_imports_inside_a_function_body(name):
    tree = ast.parse((PACKAGE / f"{name}.py").read_text())
    assert _function_local_imports(tree) == []


def _import_graph() -> dict[str, set[str]]:
    graph = {}
    for name in MODULES:
        tree = ast.parse((PACKAGE / f"{name}.py").read_text())
        edges = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                # `from . import a, b` names the modules; `from .a import x` names one.
                edges |= (
                    {a.name for a in node.names}
                    if node.module is None
                    else {node.module}
                )
        graph[name] = {e for e in edges if e in set(MODULES)}
    return graph


def test_the_import_graph_is_acyclic():
    graph = _import_graph()
    state: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = " -> ".join(stack[stack.index(node) :] + [node])
            raise AssertionError(f"import cycle: {cycle}")
        state[node] = 1
        for nxt in sorted(graph[node]):
            visit(nxt, stack + [nxt])
        state[node] = 2

    for node in sorted(graph):
        visit(node, [node])
