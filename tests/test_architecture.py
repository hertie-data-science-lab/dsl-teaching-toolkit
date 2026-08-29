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


def _sibling_module_names(tree: ast.AST) -> set[str]:
    """Names this module binds to another module OF THE PACKAGE - `from . import x`,
    `from dsl_course import x`. A name bound to anything else has no module whose
    attributes we could check."""
    bound = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if (node.level == 1 and node.module is None) or (
            node.level == 0 and node.module == "dsl_course"
        ):
            bound |= {a.asname or a.name for a in node.names if a.name in set(MODULES)}
    return bound


def _rebound_names(tree: ast.AST) -> set[str]:
    """Every name the module binds to something else somewhere: an assignment, a
    parameter, a loop or `with` target. A local `schedule = ...` shadows the import, so
    `schedule.anything` says nothing about the module any more."""
    rebound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            rebound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            rebound |= {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
            rebound |= {x.arg for x in (a.vararg, a.kwarg) if x}
    return rebound


@pytest.mark.parametrize("name", MODULES)
def test_every_sibling_module_attribute_exists(name):
    """`status.py` read `sync_faculty.COHORT_CONFIG_REPO` for months after the constant
    moved to `course`: a name that survives the move only in the *referencing* module
    stays invisible until someone runs the line. Nothing but the module itself knows
    what it exports, so ask it."""
    tree = ast.parse((PACKAGE / f"{name}.py").read_text())
    siblings = _sibling_module_names(tree) - _rebound_names(tree)
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id not in siblings or not isinstance(node.ctx, ast.Load):
            continue
        module = importlib.import_module(f"dsl_course.{node.value.id}")
        if not hasattr(module, node.attr):
            missing.append(f"{name}.py:{node.lineno} {node.value.id}.{node.attr}")
    assert missing == []
