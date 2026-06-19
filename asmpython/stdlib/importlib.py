"""importlib: implementation of the import system.

In asmpython, dynamic import is not supported at runtime (all imports are
resolved at compile time). This module provides the API surface for code
that references importlib, but most functions raise NotImplementedError.

The constants and simple introspection helpers are fully implemented.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Simple wrappers that work at compile time
# ---------------------------------------------------------------------------

def import_module(name: str, package: str = "") -> object:
    """Import a module by name (raises NotImplementedError at runtime).

    In asmpython all imports are resolved statically at compile time.
    If you write ``import_module("foo")``, add ``import foo`` to the
    top of your file so the compiler can include it.
    """
    raise NotImplementedError(
        "importlib.import_module: dynamic import not supported in asmpython; "
        "use a static 'import' statement instead"
    )


def reload(module: object) -> object:
    """Reload a module (raises NotImplementedError)."""
    raise NotImplementedError("importlib.reload: not supported in asmpython")


def invalidate_caches() -> None:
    """Invalidate finder caches (no-op in asmpython)."""
    pass


def find_spec(name: str, package: str = "") -> object:
    """Find the spec for *name* (returns None in asmpython)."""
    return None


def find_loader(name: str, path: str = "") -> object:
    """Find a loader for *name* (returns None in asmpython)."""
    return None


# ---------------------------------------------------------------------------
# util sub-module equivalents (importlib.util)
# ---------------------------------------------------------------------------

def util_find_spec(name: str) -> object:
    """importlib.util.find_spec equivalent (returns None in asmpython)."""
    return None


def util_module_from_spec(spec: object) -> object:
    """importlib.util.module_from_spec equivalent (raises NotImplementedError)."""
    raise NotImplementedError("importlib.util.module_from_spec: not supported")


def util_spec_from_file_location(name: str, location: str,
                                  submodule_search_locations: list = []) -> object:
    """importlib.util.spec_from_file_location equivalent (raises NotImplementedError)."""
    raise NotImplementedError("importlib.util.spec_from_file_location: not supported")


# ---------------------------------------------------------------------------
# ModuleSpec stub
# ---------------------------------------------------------------------------

class ModuleSpec:
    """A specification for a module's import-system-related state."""

    def __init__(self, name: str, loader: object, origin: str = "",
                 loader_state: object = None, is_package: int = 0) -> None:
        self.name: str = name
        self.loader: object = loader
        self.origin: str = origin
        self.loader_state: object = loader_state
        self.submodule_search_locations: list = []
        self.has_location: int = 1 if origin else 0
        self.parent: str = ""
        self.cached: str = ""

    def __repr__(self) -> str:
        return "ModuleSpec(name=" + self.name + ", loader=" + str(self.loader) + ")"


# ---------------------------------------------------------------------------
# abc sub-module stubs (importlib.abc)
# ---------------------------------------------------------------------------

class Finder:
    """Abstract base class for import finders (stub)."""

    def find_module(self, fullname: str, path: object = None) -> object:
        return None


class Loader:
    """Abstract base class for import loaders (stub)."""

    def create_module(self, spec: ModuleSpec) -> object:
        return None

    def exec_module(self, module: object) -> None:
        pass

    def load_module(self, fullname: str) -> object:
        raise NotImplementedError("Loader.load_module: not supported")


class ResourceLoader(Loader):
    """Loader with resource access (stub)."""

    def get_data(self, path: str) -> str:
        raise NotImplementedError("ResourceLoader.get_data: not supported")


class InspectLoader(Loader):
    """Loader that can inspect modules (stub)."""

    def is_package(self, fullname: str) -> int:
        return 0

    def get_code(self, fullname: str) -> object:
        return None

    def get_source(self, fullname: str) -> str:
        return ""


class SourceLoader(InspectLoader):
    """Loader for source files (stub)."""

    def get_filename(self, name: str) -> str:
        return ""

    def path_stats(self, path: str) -> dict:
        return {}

    def set_data(self, path: str, data: str) -> None:
        pass


class MetaPathFinder(Finder):
    """Abstract base for meta path finders (stub)."""

    def find_spec(self, fullname: str, path: object, target: object = None) -> object:
        return None

    def invalidate_caches(self) -> None:
        pass


class PathEntryFinder(Finder):
    """Abstract base for path entry finders (stub)."""

    def find_spec(self, name: str, target: object = None) -> object:
        return None

    def find_loader(self, name: str) -> list:
        return [None, []]

    def invalidate_caches(self) -> None:
        pass


class FileLoader(SourceLoader):
    """Loader for files from the filesystem (stub)."""

    def __init__(self, fullname: str, path: str) -> None:
        self.name: str = fullname
        self.path: str = path

    def get_filename(self, name: str) -> str:
        return self.path

    def get_data(self, path: str) -> str:
        f = open(path, "r")
        data: str = f.read()
        f.close()
        return data

    def is_package(self, fullname: str) -> int:
        return 0


class SourceFileLoader(FileLoader):
    """Loader for Python source files (stub)."""
    pass


class SourcelessFileLoader(FileLoader):
    """Loader for Python bytecode files (stub; no bytecode in asmpython)."""
    pass
