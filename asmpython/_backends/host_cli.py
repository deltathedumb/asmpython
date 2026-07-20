"""Public CLI front-controller for native-first package resolution.

The historical parser/build implementation remains in ``_compiler.__main__``.
This front-controller removes the private PyPI command from the public surface,
installs pip/site-packages resolvers, and permits whole-program pyinbin fallback
only when a reachable source file actually performs a dynamic import.
"""
from __future__ import annotations

import ast as _host_ast
import sys
from pathlib import Path

from asmpython._compiler import __main__ as _legacy_cli
from asmpython._compiler.project import ProjectError, load_project
from asmpython._backends.host_site_packages import (
    SitePackageImportError,
    install_pyinbin_site_package_resolution,
)


_DYNAMIC_IMPORT_ATTRS = frozenset({"import_module", "load_module", "reload"})


def _is_top_level_command(value: str) -> bool:
    if value == "build":
        return True
    if value == "package":
        return True
    if value == "pypi":
        return True
    if value == "pyinbin":
        return True
    if value == "project":
        return True
    return False


def _is_dynamic_callable(
    expression: _host_ast.expr,
    module_aliases: set[str],
    callable_aliases: set[str],
) -> bool:
    if isinstance(expression, _host_ast.Name):
        return expression.id in callable_aliases
    if isinstance(expression, _host_ast.Attribute):
        return (
            expression.attr in _DYNAMIC_IMPORT_ATTRS
            and isinstance(expression.value, _host_ast.Name)
            and expression.value.id in module_aliases
        )
    if isinstance(expression, _host_ast.Call):
        # getattr(importlib, name)(...) / getattr(imp, name)(...) is itself a
        # dynamic selection of an import operation and therefore interpreter-only.
        if not isinstance(expression.func, _host_ast.Name):
            return False
        if expression.func.id != "getattr" or not expression.args:
            return False
        owner = expression.args[0]
        return isinstance(owner, _host_ast.Name) and owner.id in module_aliases
    return False


def source_uses_dynamic_import(source: str) -> bool:
    """Return whether *source* contains an actual dynamic import operation.

    The host CPython AST is used so comments and string literals containing text
    such as ``import_module(...)`` do not accidentally authorize pyinbin fallback.
    Normal ``import`` and ``from`` statements are never interpreter-backed.
    """
    try:
        tree = _host_ast.parse(source)
    except SyntaxError:
        # Native compilation owns syntax diagnostics. A parse failure by itself
        # is not permission to execute the source through pyinbin.
        return False

    module_aliases: set[str] = set()
    callable_aliases: set[str] = {"__import__"}

    for node in _host_ast.walk(tree):
        if isinstance(node, _host_ast.Import):
            for imported in node.names:
                if imported.name in ("importlib", "imp"):
                    module_aliases.add(imported.asname or imported.name)
        elif isinstance(node, _host_ast.ImportFrom):
            if node.module in ("importlib", "imp", "builtins"):
                for imported in node.names:
                    if imported.name in _DYNAMIC_IMPORT_ATTRS or imported.name == "__import__":
                        callable_aliases.add(imported.asname or imported.name)

    # Follow simple aliases such as ``loader = importlib.import_module`` or
    # ``again = loader``. Iterate to a fixed point so short alias chains work.
    changed = True
    while changed:
        changed = False
        for node in _host_ast.walk(tree):
            if not isinstance(node, _host_ast.Assign):
                continue
            if not _is_dynamic_callable(node.value, module_aliases, callable_aliases):
                continue
            for target in node.targets:
                if isinstance(target, _host_ast.Name) and target.id not in callable_aliases:
                    callable_aliases.add(target.id)
                    changed = True

    for node in _host_ast.walk(tree):
        if isinstance(node, _host_ast.Call) and _is_dynamic_callable(
            node.func,
            module_aliases,
            callable_aliases,
        ):
            return True
    return False


def _existing_source_argument(argv: list[str]) -> Path | None:
    start = 1 if argv and argv[0] == "build" else 0
    for token in argv[start:]:
        if token.startswith("-"):
            continue
        path = Path(token)
        if path.is_file() and path.suffix.lower() in (".py", ".json"):
            return path
    return None


def _entry_source_path(source_argument: Path) -> Path | None:
    if source_argument.suffix.lower() != ".json":
        return source_argument
    try:
        cfg = load_project(source_argument)
    except ProjectError:
        return None
    candidate = source_argument.resolve().parent / cfg.entry
    return candidate if candidate.is_file() else None


def source_tree_uses_dynamic_import(entry: Path) -> bool:
    """Check the statically reachable source graph for dynamic imports."""
    from asmpython._compiler.lexer import Lexer
    from asmpython._compiler.parser import Parser
    from asmpython._compiler import program

    entry = entry.resolve()
    root = program._project_root(entry)
    queue: list[Path] = [entry]
    seen: set[str] = set()
    while queue:
        path = queue.pop(0).resolve()
        path_key = str(path)
        if path_key in seen:
            continue
        seen.add(path_key)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if source_uses_dynamic_import(source):
            return True
        try:
            module = Parser(Lexer(source).tokenize()).parse()
        except Exception:
            # Native compilation will report the real parse error. Parsing
            # failure alone is not permission to execute through pyinbin.
            continue
        for imported in program._project_imports(module, path, root):
            imported_key = str(imported.resolve())
            if imported_key not in seen:
                queue.append(imported)
    return False


def prepare_argv(argv: list[str]) -> list[str]:
    """Apply public fallback policy and return argv for the legacy parser."""
    forwarded = list(argv)
    if not forwarded:
        return forwarded
    command = forwarded[0]
    is_build = command == "build" or not _is_top_level_command(command)
    if not is_build or "--no-pyinbin-fallback" in forwarded:
        return forwarded

    source_argument = _existing_source_argument(forwarded)
    if source_argument is None:
        return forwarded
    entry = _entry_source_path(source_argument)
    if entry is None:
        return forwarded
    if not source_tree_uses_dynamic_import(entry):
        forwarded.append("--no-pyinbin-fallback")
    return forwarded


def _print_top_help() -> None:
    print("Compile Python source to native code.")
    print()
    print("usage: asmpython <build|package|pyinbin|project> ...")
    print("       asmpython <source.py> [build options]")
    print()
    print("Python packages are managed by the active interpreter:")
    print("    python -m pip install <package>")
    print()
    print("Static imports resolve asmpython stdlib first, then site-packages.")
    print("Only dynamic Python imports are eligible for pyinbin fallback.")


def _reject_private_pypi_command() -> int:
    print(
        "asmpython: the private `pypi` package store was removed; install into "
        "the active Python environment with `python -m pip install <package>`",
        file=sys.stderr,
    )
    return 2


def _call_legacy_with_static_project_policy(argv: list[str]) -> int:
    """Prevent legacy ``pyinbin_imports`` metadata from forcing fallback.

    Those roots remain available when a dynamic import actually makes fallback
    eligible. For a native-only build they are dormant metadata and must not
    reject compilation before static imports are resolved.
    """
    native_only = "--no-pyinbin-fallback" in argv
    original_load_project = _legacy_cli.load_project

    def load_project_for_build(path: Path):
        cfg = original_load_project(path)
        if native_only:
            cfg.pyinbin_imports = []
        return cfg

    _legacy_cli.load_project = load_project_for_build
    try:
        return _legacy_cli.main(argv)
    finally:
        _legacy_cli.load_project = original_load_project


def main(
    argv: list[str] | None = None,
    *,
    prepare: object = None,
) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "pypi":
        return _reject_private_pypi_command()
    if raw == ["-h"] or raw == ["--help"]:
        _print_top_help()
        return 0
    prepare_call = prepare_argv if prepare is None else prepare
    try:
        prepared = prepare_call(raw)
    except SitePackageImportError as error:
        print(f"asmpython: native import resolution failed: {error}", file=sys.stderr)
        return 1
    command = prepared[0] if prepared else "build"
    is_build = command == "build" or not _is_top_level_command(command)
    if (
        is_build
        and "--no-pyinbin-fallback" not in prepared
        and _existing_source_argument(prepared) is not None
    ):
        install_pyinbin_site_package_resolution()
    return _call_legacy_with_static_project_policy(prepared)


if __name__ == "__main__":
    raise SystemExit(main())
