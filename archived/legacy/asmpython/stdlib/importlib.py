"""Portable import machinery used by the pyinbin interpreter.

The compiler still resolves ordinary imports statically, but interpreted code
can use this module for runtime imports.  The active :class:`SourceLoader` is
injected as ``__pyinbin_loader__`` by the pyinbin runtime.
"""
from __future__ import annotations


def _loader() -> object:
    try:
        return __pyinbin_loader__
    except NameError:
        raise ImportError("importlib requires an active pyinbin loader")


def resolve_name(name: str, package: str, level: int) -> str:
    if level <= 0:
        return name
    if not package:
        raise ImportError("attempted relative import with no known parent package")
    parts = package.split(".")
    if level > len(parts):
        raise ImportError("attempted relative import beyond top-level package")
    base = parts[: len(parts) - level + 1]
    return ".".join(base + ([name] if name else []))


class ModuleSpec:
    """Description of a module and the loader responsible for it."""

    def __init__(self, name: str, loader: object, *, origin: object = None,
                 loader_state: object = None, is_package: object = None) -> None:
        self.name = name
        self.loader = loader
        self.origin = origin
        self.loader_state = loader_state
        self.submodule_search_locations = [] if is_package else None
        self.has_location = bool(origin and origin != "built-in")
        self.cached = None
        self.parent = name if is_package else name.rsplit(".", 1)[0] if "." in name else ""

    def __repr__(self) -> str:
        return "ModuleSpec(name=" + repr(self.name) + ", loader=" + repr(self.loader) + ")"


def _coerce_spec(value: object, name: str) -> object:
    if value is None:
        return None
    if isinstance(value, ModuleSpec):
        return value
    is_package = getattr(value, "submodule_search_locations", None) is not None
    spec = ModuleSpec(name, getattr(value, "loader", _loader()),
                      origin=getattr(value, "origin", None), is_package=is_package)
    locations = getattr(value, "submodule_search_locations", None)
    if locations is not None:
        spec.submodule_search_locations = locations
    spec.has_location = bool(getattr(value, "has_location", spec.has_location))
    return spec


def import_module(name: str, package: str = "") -> object:
    """Import *name* through the active pyinbin loader."""
    if not isinstance(name, str) or not name:
        raise TypeError("the 'name' argument must be a non-empty string")
    if name.startswith("."):
        level = len(name) - len(name.lstrip("."))
        name = resolve_name(name[level:], package, level)
    return _loader().load(name)


def reload(module: object) -> object:
    """Re-execute a module through its pyinbin source loader."""
    if not hasattr(module, "__name__"):
        raise TypeError("reload() argument must be a module")
    return _loader().reload(module)


def invalidate_caches() -> None:
    _loader().invalidate_caches()


def find_spec(name: str, package: str = "") -> object:
    if name.startswith("."):
        name = resolve_name(name[1:], package, 1)
    loader = _loader()
    value = loader.find_spec(name)
    if value is None:
        try:
            _, origin = loader._source_for(name)
        except Exception:
            return None
        value = ModuleSpec(name, loader, origin=origin,
                           is_package=origin.endswith("__init__.py"))
    return _coerce_spec(value, name)


def find_loader(name: str, path: object = None) -> object:
    spec = find_spec(name)
    return getattr(spec, "loader", None) if spec is not None else None


def module_from_spec(spec: object) -> object:
    loader = getattr(spec, "loader", None)
    module = loader.create_module(spec) if loader is not None and hasattr(loader, "create_module") else None
    if module is None:
        module = type("Module", (), {})()
    module.__name__ = getattr(spec, "name", "")
    module.__loader__ = loader
    module.__package__ = getattr(spec, "parent", "")
    module.__spec__ = spec
    locations = getattr(spec, "submodule_search_locations", None)
    if locations is not None:
        module.__path__ = locations
    return module


def spec_from_file_location(name: str, location: str, *, loader: object = None,
                            submodule_search_locations: object = None) -> ModuleSpec:
    if loader is None:
        loader = SourceFileLoader(name, location)
    is_package = submodule_search_locations is not None
    spec = ModuleSpec(name, loader, origin=location, is_package=is_package)
    if is_package:
        spec.submodule_search_locations = submodule_search_locations
    return spec


class Finder:
    def find_module(self, fullname: str, path: object = None) -> object:
        return None


class Loader:
    def create_module(self, spec: object) -> object:
        return None

    def exec_module(self, module: object) -> None:
        return None

    def load_module(self, fullname: str) -> object:
        spec = find_spec(fullname)
        module = module_from_spec(spec)
        self.exec_module(module)
        return module


class ResourceLoader(Loader):
    def get_data(self, path: str) -> bytes:
        raise OSError(path)


class InspectLoader(Loader):
    def is_package(self, fullname: str) -> bool:
        return False

    def get_code(self, fullname: str) -> object:
        return None

    def get_source(self, fullname: str) -> str:
        return ""


class ExecutionLoader(InspectLoader):
    def get_filename(self, fullname: str) -> str:
        raise ImportError(fullname)


class SourceLoader(ExecutionLoader, ResourceLoader):
    def path_stats(self, path: str) -> dict:
        return {}

    def set_data(self, path: str, data: bytes) -> None:
        return None


class MetaPathFinder(Finder):
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        return None

    def invalidate_caches(self) -> None:
        return None


class PathEntryFinder(Finder):
    def find_spec(self, name: str, target: object = None) -> object:
        return None

    def find_loader(self, name: str) -> object:
        return None

    def invalidate_caches(self) -> None:
        return None


class FileLoader(SourceLoader):
    def __init__(self, fullname: str, path: str) -> None:
        self.name = fullname
        self.path = path

    def get_filename(self, fullname: str) -> str:
        return self.path

    def get_data(self, path: str) -> bytes:
        with open(path, "rb") as stream:
            return stream.read()

    def is_package(self, fullname: str) -> bool:
        return self.path.endswith("__init__.py")

    def exec_module(self, module: object) -> None:
        active = _loader()
        if hasattr(active, "load_file"):
            active.load_file(self.name, self.path, module)


class SourceFileLoader(FileLoader):
    pass


class SourcelessFileLoader(FileLoader):
    pass


__all__ = ["import_module", "reload", "invalidate_caches", "find_spec", "find_loader",
           "ModuleSpec", "module_from_spec", "spec_from_file_location", "resolve_name"]
