"""Source and bundle module loader for pyinbin execution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os

from asmpython._compiler.pyinbin_package import PackedModule, verify_source_bundle

from .frontend import compile_source
from .native import create_builtin_module
from .native import _Template, _TemplateInterpolation
from .vm import PyException, VMError, VirtualMachine


class _SSLMethodPlaceholder(int):
    PROTOCOL_TLS = 2
    PROTOCOL_SSLv23 = 2
    __members__ = {"PROTOCOL_TLS": 2, "PROTOCOL_SSLv23": 2}

    def __new__(cls, value=0):
        return int.__new__(cls, value)


def _safe_isinstance(value: object, class_or_tuple: object) -> bool:
    try:
        return isinstance(value, class_or_tuple)
    except (TypeError, AttributeError):
        return False


def _safe_issubclass(value: object, class_or_tuple: object) -> bool:
    try:
        return issubclass(value, class_or_tuple)
    except TypeError:
        return False


class PyinbinImportError(VMError, ImportError):
    """A source module could not be resolved or executed by pyinbin."""


class _ModuleRegistry(dict[str, SimpleNamespace]):
    """Import cache that tolerates introspection of bootstrap-only modules."""

    def __missing__(self, name: str) -> SimpleNamespace:
        if name == "builtins":
            module = SimpleNamespace(__name__=name)
            module.__import__ = lambda module_name, *args, **kwargs: None
            return module
        raise KeyError(name)


class _ModuleNamespace(SimpleNamespace):
    """Source-module namespace honoring the module-level ``__getattr__`` hook."""

    def __getattr__(self, name: str) -> object:
        fallback = self.__dict__.get("__getattr__")
        if callable(fallback):
            return fallback(name)
        raise AttributeError(name)


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
        # Keep the portable import machinery in control even when an import
        # root (for example CPython's Lib directory) also contains importlib.
        if name == "importlib":
            bundled = Path(__file__).resolve().parents[1] / "stdlib" / "importlib.py"
            if bundled.is_file():
                return bundled.read_text(encoding="utf-8"), str(bundled)
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

    def _is_package(self, name: str) -> bool:
        """Return whether *name* resolves to a package source module."""
        if self.bundle is not None:
            packed = self._bundle_modules.get(name)
            return bool(packed and packed.path.replace("\\", "/").endswith("/__init__.py"))
        parts = name.split(".")
        return any((root.joinpath(*parts) / "__init__.py").is_file() for root in self.source_roots)

    def find_spec(self, name: str) -> object | None:
        """Find a source or bootstrap module without executing source twice."""
        if name in self._modules:
            module = self._modules[name]
            return getattr(module, "__spec__", None)
        try:
            _, filename = self._source_for(name)
        except PyinbinImportError:
            builtin = create_builtin_module(name, self._modules, default_builtins(self._import))
            if builtin is None:
                return None
            return SimpleNamespace(
                name=name, loader=self, origin="built-in",
                submodule_search_locations=None, has_location=False,
                parent=name.rsplit(".", 1)[0] if "." in name else "",
            )
        package = self._is_package(name)
        return SimpleNamespace(
            name=name, loader=self, origin=filename,
            submodule_search_locations=[str(Path(filename).parent)] if package else None,
            has_location=True, parent=name if package else name.rsplit(".", 1)[0] if "." in name else "",
        )

    def create_module(self, spec: object) -> object | None:
        return None

    def exec_module(self, module: object) -> None:
        name = str(getattr(module, "__name__", ""))
        loaded = self.load(name)
        if loaded is not module and hasattr(module, "__dict__"):
            module.__dict__.update(loaded.__dict__)

    def load_file(self, name: str, filename: str, module: object) -> object:
        """Execute an explicitly located source file into a supplied module."""
        file_path = Path(filename)
        if not file_path.is_file() and self.source_roots:
            file_path = self.source_roots[0] / file_path
        source = file_path.read_text(encoding="utf-8")
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            namespace = {}
            for key, value in {
                "__name__": name, "__file__": str(file_path),
            }.items():
                setattr(module, key, value)
        namespace.setdefault("__name__", name)
        namespace.setdefault("__file__", str(file_path))
        namespace.setdefault("__doc__", None)
        namespace["__package__"] = name.rsplit(".", 1)[0] if "." in name else ""
        namespace.update(default_builtins(self._import))
        namespace["__pyinbin_import__"] = self.load
        namespace["__pyinbin_loader__"] = self
        VirtualMachine().run(compile_source(source, str(file_path)), namespace)
        return module

    def invalidate_caches(self) -> None:
        return None

    def reload(self, module: object) -> object:
        name = str(getattr(module, "__name__", ""))
        if not name:
            raise TypeError("reload() argument must be a module")
        self._modules.pop(name, None)
        return self.load(name)

    def _import(
        self,
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] | list[str] | None = None,
        level: int = 0,
    ) -> SimpleNamespace:
        """Implement the callable form of ``__import__`` for interpreted code."""
        if level:
            package = str((globals_ or {}).get("__package__", ""))
            parts = package.split(".") if package else []
            if level > len(parts) + 1:
                raise ImportError("attempted relative import beyond top-level package")
            prefix = parts[: len(parts) - level + 1]
            name = ".".join(prefix + ([name] if name else []))
        module = self.load(name)
        if fromlist:
            return module
        return self.load(name.split(".", 1)[0])

    def load(self, name: str) -> SimpleNamespace:
        if name in self._modules:
            return self._modules[name]
        if name == "__future__":
            class _Feature:
                def __init__(self, optional: tuple, mandatory: tuple | None, flag: int) -> None:
                    self.optional = optional
                    self.mandatory = mandatory
                    self.compiler_flag = flag
                def getOptionalRelease(self) -> tuple:
                    return self.optional
                def getMandatoryRelease(self) -> tuple | None:
                    return self.mandatory
            features = {
                "nested_scopes": ((2, 1, 0, "beta", 1), (2, 2, 0, "alpha", 0), 16),
                "generators": ((2, 2, 0, "alpha", 1), (2, 3, 0, "final", 0), 0),
                "division": ((2, 2, 0, "alpha", 2), (3, 0, 0, "alpha", 0), 131072),
                "absolute_import": ((2, 5, 0, "alpha", 1), (3, 0, 0, "alpha", 0), 262144),
                "with_statement": ((2, 5, 0, "alpha", 1), (2, 6, 0, "alpha", 0), 524288),
                "print_function": ((2, 6, 0, "alpha", 2), (3, 0, 0, "alpha", 0), 1048576),
                "unicode_literals": ((2, 6, 0, "alpha", 2), (3, 0, 0, "alpha", 0), 2097152),
                "barry_as_FLUFL": ((3, 1, 0, "alpha", 2), (4, 0, 0, "alpha", 0), 4194304),
                "generator_stop": ((3, 5, 0, "beta", 1), (3, 7, 0, "alpha", 0), 8388608),
                "annotations": ((3, 7, 0, "beta", 1), None, 16777216),
            }
            module = SimpleNamespace(__name__=name, all_feature_names=list(features))
            for feature_name, values in features.items():
                setattr(module, feature_name, _Feature(*values))
            self._modules[name] = module
            return module
        if name in ("importlib.util", "importlib.abc", "importlib.machinery",
                    "importlib._bootstrap", "importlib._bootstrap_external"):
            parent = self.load("importlib")
            module = SimpleNamespace(__name__=name, __package__="importlib")
            if name.endswith(".util"):
                module.find_spec = parent.find_spec
                module.module_from_spec = parent.module_from_spec
                module.spec_from_file_location = parent.spec_from_file_location
                module.resolve_name = parent.resolve_name
                module.cache_from_source = lambda path, debug_override=None, *, optimization=None: str(path) + "c"
                module.source_from_cache = lambda path: str(path)[:-1] if str(path).endswith("c") else path
            elif name.endswith(".abc"):
                for attr in ("Finder", "Loader", "ResourceLoader", "InspectLoader",
                             "ExecutionLoader", "FileLoader", "SourceLoader",
                             "SourceFileLoader", "SourcelessFileLoader", "MetaPathFinder",
                             "PathEntryFinder"):
                    if hasattr(parent, attr):
                        setattr(module, attr, getattr(parent, attr))
            elif name.endswith(".machinery"):
                for attr in ("ModuleSpec", "SourceFileLoader", "SourcelessFileLoader",
                             "FileLoader", "SourceLoader"):
                    if hasattr(parent, attr):
                        setattr(module, attr, getattr(parent, attr))
                module.SOURCE_SUFFIXES = [".py"]
                module.BYTECODE_SUFFIXES = [".pyc"]
                module.EXTENSION_SUFFIXES = []
                module.all_suffixes = lambda: [".py", ".pyc"]
                module.PathFinder = object
                module.FileFinder = object
                module.BuiltinImporter = object
                module.FrozenImporter = object
                module.WindowsRegistryFinder = object
            elif name.endswith("._bootstrap"):
                module.ModuleSpec = parent.ModuleSpec
                module.BuiltinImporter = object
                module.FrozenImporter = object
                module._gcd_import = lambda module_name, package=None, level=0: self.load(module_name)
            else:
                module.MAGIC_NUMBER = b"pyin"
                module.SOURCE_SUFFIXES = [".py"]
                module.BYTECODE_SUFFIXES = [".pyc"]
                module.EXTENSION_SUFFIXES = []
                module.SourceFileLoader = parent.SourceFileLoader
                module.SourcelessFileLoader = parent.SourcelessFileLoader
                module.FileLoader = parent.FileLoader
                module.SourceLoader = parent.SourceLoader
                module.cache_from_source = lambda path, debug_override=None, *, optimization=None: str(path) + "c"
                module.source_from_cache = lambda path: str(path)[:-1] if str(path).endswith("c") else path
                module.spec_from_file_location = parent.spec_from_file_location
            self._modules[name] = module
            setattr(parent, name.rsplit(".", 1)[1], module)
            return module
        if name == "collections.abc":
            module = self.load("_collections_abc")
            self._modules[name] = module
            return module
        builtin = create_builtin_module(name, self._modules, default_builtins(self._import))
        if builtin is not None:
            self._modules[name] = builtin
            return builtin
        parent: SimpleNamespace | None = None
        child: str | None = None
        if "." in name:
            parent_name, child = name.rsplit(".", 1)
            parent = self.load(parent_name)
        source, filename = self._source_for(name)
        module = _ModuleNamespace(__name__=name, __file__=filename)
        # Register before execution so a directly self-referential import has
        # stable module identity rather than recursively constructing modules.
        self._modules[name] = module
        namespace = module.__dict__
        namespace.setdefault("__doc__", None)
        namespace["__package__"] = name if filename.endswith("__init__.py") else name.rsplit(".", 1)[0] if "." in name else ""
        namespace.update(default_builtins(self._import))
        namespace["__pyinbin_import__"] = self.load
        namespace["__pyinbin_loader__"] = self
        namespace["__spec__"] = self.find_spec(name)
        namespace["__loader__"] = self
        if self._is_package(name):
            namespace["__path__"] = [str(Path(filename).parent)]
        if name == "ssl":
            namespace.setdefault("_SSLMethod", _SSLMethodPlaceholder)
            for enum_name in ("Options", "AlertDescription", "SSLErrorNumber"):
                namespace.setdefault(enum_name, int)
            namespace.setdefault("CERT_NONE", 0)
            namespace.setdefault("CERT_OPTIONAL", 1)
            namespace.setdefault("CERT_REQUIRED", 2)
            namespace.setdefault("PROTOCOL_TLS", 2)
            namespace.setdefault("PROTOCOL_TLS_CLIENT", 16)
            namespace.setdefault("PROTOCOL_TLS_SERVER", 17)
        if name == "_pydecimal":
            # The pure-Python decimal fallback constructs DefaultContext while
            # defining Context; seed the prototype so its bootstrap path is
            # valid before the real object is assigned at module bottom.
            namespace["DefaultContext"] = SimpleNamespace(
                prec=28, rounding=0, Emin=-999999, Emax=999999,
                capitals=1, clamp=0, traps={}, flags={},
            )
        try:
            VirtualMachine().run(compile_source(source, filename), namespace)
        except Exception:
            self._modules.pop(name, None)
            raise

        if name == "os":
            namespace["open"] = lambda file, flags, mode=0o777: os.open(file, flags, mode)

        # ``enum.global_enum`` publishes these aliases in CPython. Keep the
        # public ``re`` surface stable while the bootstrap enum metaclass is
        # still being completed.
        if name == "re":
            flags = {
                "NOFLAG": 0, "ASCII": 256, "IGNORECASE": 2, "LOCALE": 4,
                "UNICODE": 32, "MULTILINE": 8, "DOTALL": 16, "VERBOSE": 64,
                "DEBUG": 128,
            }
            for flag_name, value in flags.items():
                namespace.setdefault(flag_name, value)
        elif name == "threading":
            namespace.setdefault("excepthook", lambda args: None)
            namespace.setdefault("ExceptHookArgs", SimpleNamespace)

        if name == "importlib":
            # CPython exposes these commonly used submodules on the package
            # after importlib itself is initialized.
            self.load("importlib.util")
            self.load("importlib.abc")
            self.load("importlib.machinery")

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


def _open_compat(file: object, mode: object = "r", *args: object, **kwargs: object) -> object:
    if isinstance(mode, int):
        fd = os.open(file, mode, 0o666)
        text_mode = "r+b" if mode & os.O_RDWR else "wb" if mode & os.O_WRONLY else "rb"
        return os.fdopen(fd, text_mode)
    return open(file, mode, *args, **kwargs)


def _dynamic_globals() -> dict[str, object]:
    raise VMError("pyinbin globals marker must be handled by the VM")


def _dynamic_locals() -> dict[str, object]:
    raise VMError("pyinbin locals marker must be handled by the VM")


_dynamic_globals.__pyinbin_globals__ = True
_dynamic_locals.__pyinbin_locals__ = True


def _dynamic_super(*args: object, **kwargs: object) -> object:
    raise VMError("pyinbin super marker must be handled by the VM")


_dynamic_super.__pyinbin_super__ = True


def default_builtins(importer: object | None = None) -> dict[str, object]:
    """The small explicit bootstrap built-in surface available to bytecode."""
    return {
        "print": print, "len": len, "sum": sum, "range": range, "format": format, "open": _open_compat,
        "str": str, "repr": repr, "int": int, "float": float, "bool": bool, "bytes": bytes,
        "NotImplemented": NotImplemented,
        "bytearray": bytearray, "memoryview": memoryview, "object": object, "type": type,
        "slice": slice,
        "super": _dynamic_super,
        "list": list, "tuple": tuple, "dict": dict, "set": set, "frozenset": frozenset,
        "complex": complex, "callable": callable, "getattr": getattr, "setattr": setattr,
        "delattr": delattr, "hasattr": hasattr, "dir": dir, "vars": vars,
        "chr": chr, "ord": ord, "bin": bin, "hex": hex, "oct": oct, "ascii": ascii, "divmod": divmod,
        "isinstance": _safe_isinstance, "issubclass": _safe_issubclass, "enumerate": enumerate, "zip": zip,
        "map": map, "filter": filter, "any": any, "all": all, "min": min, "max": max,
        "abs": abs, "round": round, "pow": pow, "id": id, "hash": hash, "sorted": sorted, "reversed": reversed,
        "iter": iter, "globals": _dynamic_globals, "locals": _dynamic_locals,
        "__import__": importer or (lambda name, *args, **kwargs: None),
        "eval": _dynamic_eval, "exec": _dynamic_exec, "compile": _dynamic_compile,
        "Exception": Exception,
        "BaseException": BaseException, "RuntimeError": RuntimeError,
        "ArithmeticError": ArithmeticError,
        "WindowsError": OSError,
        "Warning": Warning, "UserWarning": UserWarning, "DeprecationWarning": DeprecationWarning,
        "PendingDeprecationWarning": PendingDeprecationWarning, "FutureWarning": FutureWarning,
        "SyntaxWarning": SyntaxWarning, "ImportWarning": ImportWarning, "ResourceWarning": ResourceWarning,
        "OSError": OSError, "IOError": OSError, "NameError": NameError,
        "FileNotFoundError": FileNotFoundError, "NotADirectoryError": NotADirectoryError,
        "UnboundLocalError": UnboundLocalError, "MemoryError": MemoryError,
        "EOFError": EOFError, "EnvironmentError": OSError,
        "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
        "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
        "StopIteration": StopIteration, "next": next,
        "ImportError": ImportError, "ModuleNotFoundError": ModuleNotFoundError,
        "AttributeError": AttributeError, "LookupError": LookupError,
        "TimeoutError": TimeoutError,
        "SystemError": SystemError, "NotImplementedError": NotImplementedError,
        "UnicodeError": UnicodeError, "UnicodeDecodeError": UnicodeDecodeError,
        "UnicodeEncodeError": UnicodeEncodeError, "UnicodeTranslateError": UnicodeTranslateError,
        "staticmethod": staticmethod, "classmethod": classmethod,
        "property": property, "AssertionError": AssertionError,
        "Template": _Template, "Interpolation": _TemplateInterpolation,
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
    module = _ModuleNamespace(__name__="__main__", __file__=str(path))
    loader._modules["__main__"] = module
    namespace = module.__dict__
    namespace.setdefault("__doc__", None)
    package = ""
    for root in import_roots or []:
        try:
            relative_parent = path.parent.relative_to(root.resolve())
        except ValueError:
            continue
        package = ".".join(relative_parent.parts)
        break
    namespace.update(default_builtins(loader._import))
    namespace.update({"__name__": "__main__", "__file__": str(path), "__package__": package, "__pyinbin_import__": loader.load})
    try:
        return VirtualMachine().run(compile_source(source, str(path)), namespace)
    except PyException as exc:
        # CPython's test runner treats module-level SkipTest as a skipped
        # module; preserve that release-gate behavior for direct execution.
        if getattr(exc.instance.cls, "__name__", "") == "SkipTest":
            return None
        raise
