"""Source and bundle module loader for pyinbin execution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from asmpython._compiler.pyinbin_package import PackedModule, verify_source_bundle

from .frontend import compile_source
from .vm import VMError, VirtualMachine


class PyinbinImportError(VMError):
    """A source module could not be resolved or executed by pyinbin."""


class SourceLoader:
    """Execute declared Python modules through ``VirtualMachine`` only."""

    def __init__(self, source_root: Path | None = None, bundle: Path | None = None) -> None:
        if source_root is None and bundle is None:
            raise ValueError("a source root or pyinbin bundle is required")
        self.source_root = source_root.resolve() if source_root is not None else None
        self.bundle = bundle.resolve() if bundle is not None else None
        self._modules: dict[str, SimpleNamespace] = {}
        self._bundle_modules: dict[str, PackedModule] = {}
        if self.bundle is not None:
            self._bundle_modules = {module.name: module for module in verify_source_bundle(self.bundle)}

    def _source_for(self, name: str) -> tuple[str, str]:
        if self.bundle is not None:
            packed = self._bundle_modules.get(name)
            if packed is None:
                raise PyinbinImportError(f"ImportError: no pyinbin module named {name!r}")
            path = self.bundle / packed.path
            return path.read_text(encoding="utf-8"), str(path)

        assert self.source_root is not None
        parts = name.split(".")
        base = self.source_root.joinpath(*parts)
        candidates = (base.with_suffix(".py"), base / "__init__.py")
        for path in candidates:
            if path.is_file():
                return path.read_text(encoding="utf-8"), str(path)
        raise PyinbinImportError(f"ImportError: no pyinbin module named {name!r}")

    def load(self, name: str) -> SimpleNamespace:
        if name in self._modules:
            return self._modules[name]
        parent: SimpleNamespace | None = None
        child: str | None = None
        if "." in name:
            parent_name, child = name.rsplit(".", 1)
            parent = self.load(parent_name)
        source, filename = self._source_for(name)
        module = SimpleNamespace(__name__=name, __file__=filename)
        # Register before execution so a directly self-referential import has
        # stable module identity rather than recursively constructing modules.
        self._modules[name] = module
        namespace = module.__dict__
        namespace.update(default_builtins())
        namespace["__pyinbin_import__"] = self.load
        try:
            VirtualMachine().run(compile_source(source, filename), namespace)
        except Exception:
            self._modules.pop(name, None)
            raise

        if parent is not None and child is not None:
            setattr(parent, child, module)
        return module


def default_builtins() -> dict[str, object]:
    """The small explicit bootstrap built-in surface available to bytecode."""
    return {"print": print, "len": len, "range": range, "str": str, "int": int, "float": float, "bool": bool}


def run_source(path: Path, *, bundle: Path | None = None) -> object:
    """Run an entry source file through pyinbin, never through ``exec``."""
    path = path.resolve()
    if not path.is_file():
        raise PyinbinImportError(f"source file not found: {path}")
    source = path.read_text(encoding="utf-8")
    loader = SourceLoader(source_root=path.parent, bundle=bundle) if bundle else SourceLoader(source_root=path.parent)
    namespace = default_builtins()
    namespace.update({"__name__": "__main__", "__file__": str(path), "__pyinbin_import__": loader.load})
    return VirtualMachine().run(compile_source(source, str(path)), namespace)
