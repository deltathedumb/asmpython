"""Source and bundle module loader for pyinbin execution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from asmpython._compiler.pyinbin_package import PackedModule, verify_source_bundle

from .frontend import compile_source
from .native import create_builtin_module
from .vm import VMError, VirtualMachine


def _safe_isinstance(value: object, class_or_tuple: object) -> bool:
    try:
        return isinstance(value, class_or_tuple)
    except TypeError:
        return False


def _safe_issubclass(value: object, class_or_tuple: object) -> bool:
    try:
        return issubclass(value, class_or_tuple)
    except TypeError:
        return False


class PyinbinImportError(VMError):
    """A source module could not be resolved or executed by pyinbin."""


class _ModuleRegistry(dict[str, SimpleNamespace]):
    """Import cache that tolerates introspection of bootstrap-only modules."""

    def __missing__(self, name: str) -> SimpleNamespace:
        module = SimpleNamespace(__name__=name)
        self[name] = module
        return module


class SourceLoader:
    """Execute declared Python modules through ``VirtualMachine`` only."""

    def __init__(
        self,
        source_root: Path | None = None,
        bundle: Path | None = None,
        import_roots: list[Path] | None = None,
    ) -> None:
        if source_root is None and not import_roots and bundle is None:
            raise ValueError("a source root or pyinbin bundle is required")
        roots = ([source_root] if source_root is not None else []) + (import_roots or [])
        self.source_roots = list(dict.fromkeys(root.resolve() for root in roots))
        self.bundle = bundle.resolve() if bundle is not None else None
        self._modules: dict[str, SimpleNamespace] = _ModuleRegistry()
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

        parts = name.split(".")
        for root in self.source_roots:
            base = root.joinpath(*parts)
            candidates = (base.with_suffix(".py"), base / "__init__.py")
            for path in candidates:
                if path.is_file():
                    return path.read_text(encoding="utf-8"), str(path)
        raise PyinbinImportError(f"ImportError: no pyinbin module named {name!r}")

    def load(self, name: str) -> SimpleNamespace:
        if name in self._modules:
            return self._modules[name]
        if name == "collections.abc":
            module = self.load("_collections_abc")
            self._modules[name] = module
            return module
        builtin = create_builtin_module(name, self._modules, default_builtins())
        if builtin is not None:
            self._modules[name] = builtin
            return builtin
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
        namespace["__package__"] = name if filename.endswith("__init__.py") else name.rsplit(".", 1)[0] if "." in name else ""
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


def _dynamic_eval(source: str, globals_: dict[str, object] | None = None, locals_: dict[str, object] | None = None) -> object:
    raise VMError("pyinbin eval marker must be handled by the VM")


def _dynamic_exec(source: str, globals_: dict[str, object] | None = None, locals_: dict[str, object] | None = None) -> None:
    raise VMError("pyinbin exec marker must be handled by the VM")


def _dynamic_compile(source: str, filename: str = "<string>", mode: str = "exec") -> object:
    raise VMError("pyinbin compile marker must be handled by the VM")


_dynamic_eval.__pyinbin_eval__ = True
_dynamic_exec.__pyinbin_exec__ = True
_dynamic_compile.__pyinbin_compile__ = True


def _dynamic_globals() -> dict[str, object]:
    raise VMError("pyinbin globals marker must be handled by the VM")


def _dynamic_locals() -> dict[str, object]:
    raise VMError("pyinbin locals marker must be handled by the VM")


_dynamic_globals.__pyinbin_globals__ = True
_dynamic_locals.__pyinbin_locals__ = True


def default_builtins() -> dict[str, object]:
    """The small explicit bootstrap built-in surface available to bytecode."""
    return {
        "print": print, "len": len, "sum": sum, "range": range, "format": format, "open": open,
        "str": str, "repr": repr, "int": int, "float": float, "bool": bool, "bytes": bytes,
        "bytearray": bytearray, "memoryview": memoryview, "object": object, "type": type,
        "list": list, "tuple": tuple, "dict": dict, "set": set, "frozenset": frozenset,
        "complex": complex, "callable": callable, "getattr": getattr, "setattr": setattr,
        "delattr": delattr, "hasattr": hasattr, "dir": dir, "vars": vars,
        "chr": chr, "ord": ord, "bin": bin, "hex": hex, "oct": oct, "ascii": ascii,
        "isinstance": _safe_isinstance, "issubclass": _safe_issubclass, "enumerate": enumerate, "zip": zip,
        "map": map, "filter": filter, "any": any, "all": all, "min": min, "max": max,
        "abs": abs, "round": round, "id": id, "hash": hash, "sorted": sorted, "reversed": reversed,
        "iter": iter, "globals": _dynamic_globals, "locals": _dynamic_locals,
        "__import__": lambda name, *args, **kwargs: None,
        "eval": _dynamic_eval, "exec": _dynamic_exec, "compile": _dynamic_compile,
        "Exception": Exception,
        "BaseException": BaseException, "RuntimeError": RuntimeError,
        "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
        "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
        "StopIteration": StopIteration, "next": next,
        "ImportError": ImportError, "AttributeError": AttributeError, "LookupError": LookupError,
        "SystemError": SystemError, "NotImplementedError": NotImplementedError,
        "UnicodeError": UnicodeError, "UnicodeDecodeError": UnicodeDecodeError,
        "UnicodeEncodeError": UnicodeEncodeError, "UnicodeTranslateError": UnicodeTranslateError,
        "staticmethod": staticmethod, "classmethod": classmethod,
        "property": property, "AssertionError": AssertionError,
    }


def run_source(
    path: Path,
    *,
    bundle: Path | None = None,
    import_roots: list[Path] | None = None,
) -> object:
    """Run an entry source file through pyinbin, never through ``exec``."""
    path = path.resolve()
    if not path.is_file():
        raise PyinbinImportError(f"source file not found: {path}")
    source = path.read_text(encoding="utf-8")
    loader = SourceLoader(source_root=path.parent, bundle=bundle, import_roots=import_roots)
    namespace = default_builtins()
    namespace.update({"__name__": "__main__", "__file__": str(path), "__pyinbin_import__": loader.load})
    return VirtualMachine().run(compile_source(source, str(path)), namespace)
