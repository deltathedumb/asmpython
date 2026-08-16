"""Finding the tests, and expanding their cases.

Collection is by NAME -- `test_*.py`, `Test*`, `test_*` -- because a naming
rule needs no registration step, and a registration step is a thing to forget.

A collected `Test` is a plain record with everything needed to run it and
nothing that cannot cross a process boundary except the callable itself, which
each worker re-imports. That split is what lets the pool address a test by id
rather than shipping it.
"""
from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .api import AUTOUSE, CASES, FIXTURE, NEEDS, SKIP


@dataclass
class Test:
    """One runnable test, already expanded from its cases."""

    #: `module::Class::name[case]` -- stable, unique, and what the CLI's
    #: filter matches against.
    id: str
    module: str
    cls: str | None
    name: str
    #: Parameter values from `cases`, by name.
    args: dict[str, Any] = field(default_factory=dict)
    skip: str | None = None
    #: Named guards this test needs -- see `run.GUARDS`. A failing guard
    #: blocks every test that declared it, without running any of them.
    needs: tuple[str, ...] = ()
    #: Roughly how long it took last time, in seconds. Used only for ordering:
    #: starting the slowest first packs the workers better, because a long
    #: test discovered last leaves every other core idle waiting for it.
    weight: float = 0.0

    def resolve(self) -> tuple[Callable, Any]:
        """The function to call and the instance to bind it to, if any."""
        module = importlib.import_module(self.module)
        if self.cls is None:
            return getattr(module, self.name), None
        owner = getattr(module, self.cls)
        return getattr(owner, self.name), owner()


def _expand(target) -> list[dict]:
    """Every combination of the `cases` stacked on `target`.

    The OUTERMOST decorator varies slowest, which is what makes two stacked
    `cases` read as a nested loop written the way it is written.
    """
    stacked = list(getattr(target, CASES, ()))
    if not stacked:
        return [{"suffix": "", "args": {}}]
    out = [{"suffix": "", "args": {}}]
    for names, values in stacked:
        grown = []
        for prefix in out:
            for value in values:
                got = getattr(value, "values", None)
                given = list(got) if got is not None else (
                    list(value) if len(names) > 1 else [value])
                label = getattr(value, "id", None) or "-".join(
                    _label(v) for v in given)
                merged = dict(prefix["args"])
                merged.update(dict(zip(names, given)))
                grown.append({
                    "suffix": f"{prefix['suffix']}-{label}" if prefix["suffix"]
                              else label,
                    "args": merged,
                })
        out = grown
    return out


def _label(value) -> str:
    """A case's name. Short and readable beats exact: the id only has to be
    unique within one test, and a whole program as an id is unusable."""
    if isinstance(value, str):
        text = value if len(value) <= 24 else value[:21] + "..."
        return "".join(c if c.isalnum() or c in "-_." else "_" for c in text)
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    return type(value).__name__


def _fixtures_of(module) -> dict[str, Callable]:
    return {name: obj for name, obj in vars(module).items()
            if callable(obj) and getattr(obj, FIXTURE, False)}


def autouse_of(module) -> list[Callable]:
    return [f for f in _fixtures_of(module).values()
            if getattr(f, AUTOUSE, False)]


def fixture_named(module, name: str):
    return _fixtures_of(module).get(name)


def _module_names(root: Path, paths: list[Path]) -> list[str]:
    """Import names for the files found, rooted at the repository."""
    out = []
    for path in paths:
        rel = path.relative_to(root).with_suffix("")
        out.append(".".join(rel.parts))
    return out


def collect(root: Path, targets: list[str] | None = None) -> list[Test]:
    """Every test under `targets`, expanded.

    Importing the modules is unavoidable -- the cases are values, and a value
    exists only once the module has run -- so a module that fails to import is
    a collection error and stops the run rather than quietly contributing no
    tests. A suite that silently shrinks is worse than one that fails.
    """
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

    files: list[Path] = []
    for target in targets or ["tests"]:
        here = root / target
        if here.is_file():
            files.append(here)
        else:
            files.extend(sorted(here.rglob("test_*.py")))

    tests: list[Test] = []
    for name in _module_names(root, files):
        module = importlib.import_module(name)
        for attr, obj in vars(module).items():
            if attr.startswith("test_") and inspect.isfunction(obj):
                tests.extend(_from_function(name, None, attr, obj, obj))
            elif attr.startswith("Test") and inspect.isclass(obj):
                for inner, method in vars(obj).items():
                    if not inner.startswith("test_"):
                        continue
                    if not inspect.isfunction(method):
                        continue
                    tests.extend(_from_function(name, attr, inner, method, obj))
    return tests


def _from_function(module: str, cls: str | None, name: str, func,
                   case_owner) -> list[Test]:
    """One test per case. A class-level `cases` distributes over its methods,
    so the owner's decorators are expanded alongside the method's own."""
    stacked = list(getattr(func, CASES, ()))
    if case_owner is not func:
        stacked = list(getattr(case_owner, CASES, ())) + stacked
    holder = type("_", (), {})()
    setattr(holder, CASES, stacked)
    base = f"{module}::{cls}::{name}" if cls else f"{module}::{name}"
    reason = getattr(func, SKIP, None) or (
        getattr(case_owner, SKIP, None) if case_owner is not func else None)
    needs = tuple(getattr(func, NEEDS, ())) + (
        tuple(getattr(case_owner, NEEDS, ()))
        if case_owner is not func else ())
    return [
        Test(id=f"{base}[{case['suffix']}]" if case["suffix"] else base,
             module=module, cls=cls, name=name, args=case["args"],
             skip=reason, needs=needs)
        for case in _expand(holder)
    ]
