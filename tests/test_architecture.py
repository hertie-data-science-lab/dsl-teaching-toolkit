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
# Dotted names below the package: `console`, and `ops.registry` for a module of the `ops`
# subpackage (its `__init__` is `ops`).
MODULES = sorted(
    m.name.removeprefix("dsl_course.")
    for m in pkgutil.walk_packages([str(PACKAGE)], prefix="dsl_course.")
)


def _source(name: str) -> Path:
    base = PACKAGE.joinpath(*name.split("."))
    return base / "__init__.py" if base.is_dir() else base.with_suffix(".py")


def _package_of(name: str) -> list[str]:
    """The package a module's relative imports are resolved against."""
    parts = name.split(".")
    return parts if _source(name).name == "__init__.py" else parts[:-1]


def _relative_targets(name: str, node: ast.ImportFrom) -> set[str]:
    """The package modules a relative `from ... import` names, as dotted names."""
    base = _package_of(name)
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    if node.module is None:
        return {".".join([*base, a.name]) for a in node.names}
    return {".".join([*base, *node.module.split(".")])}


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
    tree = ast.parse(_source(name).read_text())
    assert _function_local_imports(tree) == []


def _import_graph() -> dict[str, set[str]]:
    graph = {}
    for name in MODULES:
        tree = ast.parse(_source(name).read_text())
        edges = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                # `from . import a, b` names the modules; `from .a import x` names one.
                edges |= _relative_targets(name, node)
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


def _sibling_module_names(name: str, tree: ast.AST) -> dict[str, str]:
    """Names this module binds to another module OF THE PACKAGE - `from . import x`,
    `from .. import x`, `from dsl_course import x` - mapped to that module's dotted name.
    A name bound to anything else has no module whose attributes we could check."""
    bound = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level and node.module is None:
            targets = sorted(_relative_targets(name, node))
        elif node.level == 0 and node.module == "dsl_course":
            targets = [a.name for a in node.names]
        else:
            continue
        for alias, target in zip(node.names, targets):
            if target in set(MODULES):
                bound[alias.asname or alias.name] = target
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
    tree = ast.parse(_source(name).read_text())
    rebound = _rebound_names(tree)
    siblings = {
        k: v for k, v in _sibling_module_names(name, tree).items() if k not in rebound
    }
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id not in siblings or not isinstance(node.ctx, ast.Load):
            continue
        module = importlib.import_module(f"dsl_course.{siblings[node.value.id]}")
        if not hasattr(module, node.attr):
            missing.append(f"{name}.py:{node.lineno} {node.value.id}.{node.attr}")
    assert missing == []
