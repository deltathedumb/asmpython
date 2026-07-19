"""Public CLI front-controller for native-first package resolution.

The historical parser/build implementation remains in ``_compiler.__main__``.
This front-controller removes the private PyPI command from the public surface,
installs pip/site-packages resolvers, and permits whole-program pyinbin fallback
only when a reachable source file actually performs a dynamic import.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import __main__ as _legacy_cli
from .project import ProjectError, load_project
from .site_packages import install_pyinbin_site_package_resolution


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


def _compact_source(source: str) -> str:
    out: list[str] = []
    for ch in source:
        if ch not in (" ", "\t", "\r", "\n"):
            out.append(ch)
    return "".join(out)


def source_uses_dynamic_import(source: str) -> bool:
    """Conservatively detect import operations requiring an interpreter.

    Normal ``import``/``from`` statements are deliberately absent: those must
    resolve natively through the bundled stdlib or pip's site-packages.  A false
    positive only allows a fallback after native compilation rejects the source;
    it never bypasses the native attempt by itself.
    """
    compact = _compact_source(source)
    if "__import__(" in compact:
        return True
    if "import_module(" in compact:
        return True
    if "load_module(" in compact:
        return True
    if "importlib.reload(" in compact:
        return True
    if "getattr(importlib," in compact:
        return True
    if "getattr(imp," in compact:
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
    from .lexer import Lexer
    from .parser import Parser
    from . import program

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
            # Native compilation will report the real parse error.  Parsing
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
    eligible.  For a native-only build they are merely dormant metadata and must
    not reject compilation before static imports are resolved.
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


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "pypi":
        return _reject_private_pypi_command()
    if raw == ["-h"] or raw == ["--help"]:
        _print_top_help()
        return 0
    prepared = prepare_argv(raw)
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
