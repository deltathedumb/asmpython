"""Native resolution for packages installed by the host Python's pip.

The native compiler keeps its own bundled stdlib authoritative.  Only when an
absolute import is neither a project module nor an asmpython stdlib module do we
look through the active interpreter's site-packages/dist-packages roots.  Pure
Python modules are merged by the existing whole-program loader; compiled CPython
extension modules are rejected explicitly because neither the native runtime nor
pyinbin implements the CPython C extension ABI.

This module is installed as a small extension around ``program.py`` rather than
forking its whole-program merge logic.  It patches all three resolution paths the
loader uses: import discovery, relative imports, and value-import materialization.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import ast_nodes as A
from .lexer import Lexer
from .parser import Parser
from . import program


class SitePackageImportError(RuntimeError):
    """A pip-installed module was found but cannot be compiled natively."""


def _append_unique(paths: list[Path], candidate: Path) -> None:
    resolved = candidate.resolve()
    for existing in paths:
        if existing == resolved:
            return
    paths.append(resolved)


def site_package_roots() -> list[Path]:
    """Return import roots belonging to the active interpreter's pip installs.

    Normal and user installs appear on ``sys.path`` as ``site-packages`` or
    ``dist-packages``.  Legacy ``.egg`` entries are accepted too.  Plain path
    entries added by ``.pth`` files are recovered from those files so editable
    installs continue to work without treating the entire CPython stdlib as a
    native-import source.
    """
    roots: list[Path] = []
    for raw in getattr(sys, "path", []) or []:
        if not raw:
            continue
        path = Path(raw)
        lower_name = path.name.lower()
        if lower_name in ("site-packages", "dist-packages") or path.suffix.lower() == ".egg":
            if path.is_dir():
                _append_unique(roots, path)

    base_roots: list[Path] = list(roots)
    for root in base_roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix.lower() != ".pth" or not entry.is_file():
                continue
            try:
                lines = entry.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith("import "):
                    continue
                candidate = Path(line)
                if not candidate.is_absolute():
                    candidate = root / candidate
                if candidate.is_dir():
                    _append_unique(roots, candidate)
    return roots


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _site_root_for(path: Path) -> Path | None:
    for root in site_package_roots():
        if _within(path, root):
            return root
    return None


def _is_ffi_stdlib(module: str) -> bool:
    """Inline mirror of ``asmpython.stdlib.STDLIB_BINDINGS`` module keys.

    Keeping this as scalar comparisons avoids depending on a module-level dict
    when the compiler is self-hosted.  A focused test compares it against the
    live registry so additions cannot silently become shadowable by site-packages.
    """
    top = module.split(".")[0]
    if top == "math":
        return True
    if top == "os":
        return True
    if top == "sys":
        return True
    if top == "time":
        return True
    if top == "random":
        return True
    if top == "socket":
        return True
    if top == "_threadingffi":
        return True
    if top == "_gui_sdl":
        return True
    if top == "_gui_ttf":
        return True
    if top == "_audio_sdl":
        return True
    if top == "network":
        return True
    if top == "hardware":
        return True
    return False


def _is_asmpython_stdlib(module: str) -> bool:
    if _is_ffi_stdlib(module):
        return True
    top = module.split(".")[0]
    if program._is_bundled_source_stdlib(top):
        return True
    return program._resolve_bundled_stdlib(module) is not None


def _module_target(root: Path, module: str) -> Path:
    target = root
    for part in module.split("."):
        if part:
            target = target / part
    return target


def _native_extension_at(target: Path) -> Path | None:
    parent = target.parent
    if not parent.is_dir():
        return None
    stem = target.name.lower()
    try:
        entries = list(parent.iterdir())
    except OSError:
        return None
    for entry in entries:
        name = entry.name.lower()
        if not (name == stem + ".pyd" or name == stem + ".so" or name == stem + ".dylib" or name.startswith(stem + ".")):
            continue
        if name.endswith(".pyd") or name.endswith(".so") or name.endswith(".dylib"):
            return entry
    return None


def _validate_python_source(path: Path, module: str) -> Path:
    try:
        source = path.read_text(encoding="utf-8")
        Parser(Lexer(source).tokenize()).parse()
    except Exception as exc:
        raise SitePackageImportError(
            f"pip-installed module {module!r} at {path} cannot be compiled "
            f"natively: {exc}"
        ) from exc
    return path


def _resolve_target(target: Path, module: str) -> Path | None:
    py = Path(str(target) + ".py")
    if py.is_file():
        return _validate_python_source(py, module)
    init = target / "__init__.py"
    if init.is_file():
        return _validate_python_source(init, module)
    native = _native_extension_at(target)
    if native is not None:
        raise SitePackageImportError(
            f"pip-installed module {module!r} resolves to CPython extension "
            f"{native}; native asmpython does not implement the CPython C-API"
        )
    return None


def resolve_site_package(module: str) -> Path | None:
    """Resolve an absolute module strictly from pip-managed import roots."""
    if not module or _is_asmpython_stdlib(module):
        return None
    for root in site_package_roots():
        resolved = _resolve_target(_module_target(root, module), module)
        if resolved is not None:
            return resolved
    return None


def _resolve_external(module: str, importer: Path, root: Path) -> Path | None:
    # Project-local code remains authoritative over third-party packages.
    if program._resolve_absolute(module, root) is not None:
        return None
    if program._resolve_user_module(module, importer, root) is not None:
        return None
    # asmpython's source and FFI stdlib always wins over site-packages.
    if _is_asmpython_stdlib(module):
        return None
    return resolve_site_package(module)


def _relative_target(importer: Path, level: int, module: str) -> Path:
    base = importer.parent
    for _ in range(level - 1):
        base = base.parent
    target = base
    if module:
        for part in module.split("."):
            target = target / part
    return target


def install_native_import_resolution() -> None:
    """Extend ``program.py`` with site-packages resolution, once per process."""
    if getattr(program, "_site_packages_resolution_installed", False):
        return

    original_relative = program._resolve_relative
    original_project_imports = program._project_imports
    original_fromimport = program._resolve_fromimport_path

    def resolve_relative(importer: Path, level: int, module: str, root: Path) -> Path | None:
        resolved = original_relative(importer, level, module, root)
        if resolved is not None:
            return resolved
        site_root = _site_root_for(importer)
        if site_root is None:
            return None
        target = _relative_target(importer, level, module)
        if not _within(target, site_root):
            return None
        return _resolve_target(target, "." * level + module)

    def add_path(out: list[Path], path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        for existing in out:
            if existing.resolve() == resolved:
                return
        out.append(path)

    def project_imports(module: A.Module, importer: Path, root: Path) -> list[Path]:
        out = original_project_imports(module, importer, root)
        for stmt in program._collect_import_stmts(module):
            if isinstance(stmt, A.Import):
                add_path(out, _resolve_external(stmt.module, importer, root))
                continue
            if not isinstance(stmt, A.FromImport):
                continue

            original_names = stmt.orig_names if stmt.orig_names else stmt.names
            if stmt.level > 0:
                if _site_root_for(importer) is None:
                    continue
                if stmt.module:
                    add_path(out, resolve_relative(importer, stmt.level, stmt.module, root))
                    for original_name in original_names:
                        dotted = stmt.module + "." + original_name
                        add_path(out, resolve_relative(importer, stmt.level, dotted, root))
                else:
                    for original_name in original_names:
                        add_path(out, resolve_relative(importer, stmt.level, original_name, root))
                    package_init = _relative_target(importer, stmt.level, "") / "__init__.py"
                    if package_init.is_file():
                        add_path(out, _validate_python_source(package_init, "." * stmt.level))
                continue

            if not stmt.module:
                continue
            # Imported names may be submodules or values from the package/module.
            for original_name in original_names:
                add_path(
                    out,
                    _resolve_external(stmt.module + "." + original_name, importer, root),
                )
            add_path(out, _resolve_external(stmt.module, importer, root))
        return out

    def resolve_fromimport_path(
        stmt: A.FromImport, importer: Path, root: Path
    ) -> Path | None:
        resolved = original_fromimport(stmt, importer, root)
        if resolved is not None:
            return resolved
        if stmt.level > 0:
            if _site_root_for(importer) is None:
                return None
            if stmt.module:
                return resolve_relative(importer, stmt.level, stmt.module, root)
            package_init = _relative_target(importer, stmt.level, "") / "__init__.py"
            if package_init.is_file():
                return _validate_python_source(package_init, "." * stmt.level)
            return None
        if stmt.module:
            return _resolve_external(stmt.module, importer, root)
        return None

    program._resolve_relative = resolve_relative
    program._project_imports = project_imports
    program._resolve_fromimport_path = resolve_fromimport_path
    program._site_packages_resolution_installed = True


def install_pyinbin_site_package_resolution() -> None:
    """Give dynamic pyinbin imports the active interpreter's pip roots.

    This does not make static imports interpreter-backed.  The native whole-
    program loader gets first chance at every normal import; this wrapper is
    used only after the public CLI has established that the reachable source
    tree contains a dynamic import operation.
    """
    import asmpython.pyinbin as pyinbin
    from asmpython.pyinbin import loader as pyinbin_loader

    if getattr(pyinbin, "_site_packages_resolution_installed", False):
        return
    original_run_source = pyinbin_loader.run_source

    def run_source(
        path: Path,
        *,
        bundle: Path | None = None,
        import_roots: list[Path] | None = None,
    ) -> object:
        roots: list[Path] = []
        for root in import_roots or []:
            _append_unique(roots, root)
        for root in site_package_roots():
            _append_unique(roots, root)
        return original_run_source(
            path,
            bundle=bundle,
            import_roots=roots or None,
        )

    pyinbin_loader.run_source = run_source
    pyinbin.run_source = run_source
    pyinbin._site_packages_resolution_installed = True
