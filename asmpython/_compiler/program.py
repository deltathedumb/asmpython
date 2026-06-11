"""Whole-program loader: discover, parse, and merge a project's modules.

asmpython compiles a *program*, not a single file. When the entry module
imports classes/functions from sibling project files (`from .errors import
SourcePos`, `from . import ast_nodes as A`), those definitions must be visible
to sema and codegen — otherwise constructing `SourcePos(...)` or calling an
inherited method fails because the class lives in another file.

`load_program` walks the import graph starting at the entry file, parses every
reachable *project* module, and merges their top-level functions and classes
into one `Module`. The result is a single compilation unit the existing
sema/codegen pipeline handles unchanged. Third-party / stdlib imports
(`math`, `os`, things outside the project root) are left alone — they're
resolved through the FFI registry as before.

This is "whole-program compilation": no per-file `.o` linking, no cross-file
symbol ABI — every project module's code ends up in one `.asm`.
"""

from __future__ import annotations

from pathlib import Path

from .lexer import Lexer
from .parser import Parser
from . import ast_nodes as A


def _resolve_relative(importer: Path, level: int, module: str, root: Path) -> Path | None:
    """Resolve a relative import (`from ..pkg.mod import x`) to a file path.

    `level` is the dot count: 1 = same package dir, 2 = parent, etc. `module`
    is the dotted remainder (may be ""). Returns the `.py` file or None if it
    doesn't resolve to a project file (or escapes the project root).
    """
    base = importer.parent
    # Each extra dot beyond the first walks up one directory.
    for _ in range(level - 1):
        base = base.parent
    target = base
    if module:
        for part in module.split("."):
            target = target / part
    candidate = target.with_suffix(".py") if target.suffix == "" else target
    py = candidate if candidate.suffix == ".py" else Path(str(candidate) + ".py")
    if py.is_file() and _within(py, root):
        return py
    # `from . import submod` — module is "", the imported *name* is the module.
    return None


def _resolve_absolute(module: str, root: Path) -> Path | None:
    """Resolve an absolute dotted import (`asmpython._compiler.errors`) to a
    project file under `root`'s parent, or None if it's not a project module."""
    parts = module.split(".")
    # The project root dir is named after the top package (e.g. `asmpython`),
    # so an import `asmpython.x.y` maps to <root>/x/y.py with root.name == parts[0].
    if not parts:
        return None
    if parts[0] != root.name:
        return None
    target = root
    for part in parts[1:]:
        target = target / part
    py = Path(str(target) + ".py")
    if py.is_file():
        return py
    # A package import (`asmpython._compiler`) -> its __init__.py.
    init = target / "__init__.py"
    if init.is_file():
        return init
    return None


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _project_imports(module: A.Module, importer: Path, root: Path) -> list[Path]:
    """Every project `.py` file the module imports (relative or absolute)."""
    out: list[Path] = []
    for stmt in module.body:
        if isinstance(stmt, A.FromImport):
            if stmt.level > 0:
                if stmt.module:
                    p = _resolve_relative(importer, stmt.level, stmt.module, root)
                    if p is not None:
                        out.append(p)
                else:
                    # `from . import a, b` — each name is a sibling module.
                    base = importer.parent
                    for _ in range(stmt.level - 1):
                        base = base.parent
                    for name in stmt.names:
                        py = base / f"{name}.py"
                        if py.is_file() and _within(py, root):
                            out.append(py)
            elif stmt.module:
                p = _resolve_absolute(stmt.module, root)
                if p is not None:
                    out.append(p)
        elif isinstance(stmt, A.Import):
            p = _resolve_absolute(stmt.module, root)
            if p is not None:
                out.append(p)
    return out


def _project_root(entry: Path) -> Path:
    """The top-level package directory: walk up while a parent has __init__.py
    so the whole `asmpython/` tree counts as one project."""
    root = entry.parent
    while (root.parent / "__init__.py").is_file() or (root / "__init__.py").is_file():
        if (root.parent / "__init__.py").is_file():
            root = root.parent
        else:
            break
    return root


def load_program(entry_src: str, entry_path: Path) -> A.Module:
    """Parse the entry module and every reachable project module, merging their
    top-level funcs and classes into the entry Module. Returns the merged unit.

    The entry module's own `body` (top-level statements) is preserved as the
    program's main code; imported modules contribute only their definitions
    (their module-level side-effecting statements are not run — matching how a
    compiled program treats imports as definition sources, not executed bodies,
    for the self-host subset).
    """
    entry_path = entry_path.resolve()
    root = _project_root(entry_path)

    entry = Parser(Lexer(entry_src).tokenize()).parse()

    seen: set[Path] = {entry_path}
    # Names already defined so merges don't duplicate (first definition wins).
    func_names = {f.name for f in entry.funcs}
    class_names = {c.name for c in entry.classes}

    queue = _project_imports(entry, entry_path, root)
    while queue:
        mod_path = queue.pop(0).resolve()
        if mod_path in seen:
            continue
        seen.add(mod_path)
        try:
            mod_src = mod_path.read_text(encoding="utf-8")
            mod = Parser(Lexer(mod_src).tokenize()).parse()
        except Exception:
            # A module we can't parse is skipped — it may be third-party-ish or
            # use constructs outside the subset; the importer still type-checks
            # leniently against the missing name.
            continue
        for f in mod.funcs:
            if f.name not in func_names:
                func_names.add(f.name)
                entry.funcs.append(f)
        for c in mod.classes:
            if c.name not in class_names:
                class_names.add(c.name)
                entry.classes.append(c)
        # Recurse into this module's own project imports.
        for p in _project_imports(mod, mod_path, root):
            if p.resolve() not in seen:
                queue.append(p)

    return entry
