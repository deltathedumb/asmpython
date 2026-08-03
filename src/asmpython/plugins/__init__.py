"""Third-party backends, targets, toolchains and frontends.

    # my_plugin_module.py
    from asmpython.plugins import Plugin, Backend, Target, Frontend, Linker

    plugin = Plugin("mypack")
    plugin.backends.append(MyBackend())
    plugin.targets.append(Target("my-machine", arch="my"))

    __asmpython_plugin__ = plugin

then:

    asmpython plugin add my_plugin_module      # remembered from now on
    asmpython plugin list
    asmpython build prog.py --backend my-backend

The four registries have always accepted an outside registration --
`register()` is deliberately the same call the built-ins make. What this adds
is REACHABILITY: registration is a side effect of importing a module, and
nothing imported anyone else's, so a correct third-party backend was reported
as `unknown backend` from the command line while working perfectly when
asmpython was embedded as a library.

Four ways a module gets imported, in this order:

    1. `asmpython plugin add NAME`      -- persisted; loaded on every run
    2. `--plugin NAME`                  -- this invocation only
    3. `ASMPYTHON_PLUGINS=a:b`          -- for a CI job or a Makefile
    4. an `asmpython.plugins` entry point of an installed distribution

A module may either declare `__asmpython_plugin__` or call `register()` at
import time; both work. Declaring is better, because `asmpython plugin show`
can then say what a module provides WITHOUT letting it change the compiler's
state, and so can the conflict check when installing.

FAILURES ARE REPORTED, NOT SWALLOWED. A plugin that will not import is an
error naming itself: carrying on without it produces `unknown backend` for a
backend the user is looking at in their own source file, which sends them to
debug entirely the wrong thing.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from . import store
from .manifest import (
    Backend,
    CompilerPatch,
    Frontend,
    Linker,
    Plugin,
    Target,
    TargetEntry,
    Toolchain,
)
from .patch import GUARDED, SEALED, PatchError, applied, revert_all
from .resolve import Resolution, ResolveError, Sources, resolve

#: The attribute a module sets to declare what it provides.
MANIFEST_ATTR = "__asmpython_plugin__"

#: Distributions advertise plugins under this entry-point group.
ENTRY_POINT_GROUP = "asmpython.plugins"

#: Colon- or semicolon-separated modules to import, for environments where a
#: flag is awkward.
ENV_VAR = "ASMPYTHON_PLUGINS"

__all__ = [
    "ENTRY_POINT_GROUP",
    "ENV_VAR",
    "GUARDED",
    "MANIFEST_ATTR",
    "SEALED",
    "Backend",
    "CompilerPatch",
    "Frontend",
    "Linker",
    "LoadReport",
    "PatchError",
    "Plugin",
    "PluginError",
    "Resolution",
    "ResolveError",
    "Sources",
    "Target",
    "TargetEntry",
    "Toolchain",
    "applied",
    "install",
    "installed",
    "load",
    "load_all",
    "manifest_of",
    "resolve",
    "revert_all",
    "store",
    "uninstall",
]

#: Modules already loaded in this process. Registries refuse a duplicate
#: backend name, so this is load-bearing: a plugin that is both installed and
#: named with `--plugin` would otherwise be a crash.
_loaded: set[str] = set()


class PluginError(Exception):
    """A plugin was named and could not be loaded."""

    def __init__(self, name: str, cause: BaseException) -> None:
        super().__init__(f"plugin {name!r} failed to load: {cause}")
        self.name = name
        self.cause = cause


def manifest_of(module: ModuleType) -> Plugin | None:
    """The `Plugin` a module declares, if it declares one."""
    found = getattr(module, MANIFEST_ATTR, None)
    return found if isinstance(found, Plugin) else None


def load(
    name: str, sources: Sources | None = None, root: Path | None = None
) -> Plugin | None:
    """Import one plugin and register what it provides.

    Returns its manifest, or None for a module that registered itself at
    import time in the older style.
    """
    if name in _loaded:
        return None
    try:
        found = resolve(name, sources or Sources(), root)
    except ResolveError as exc:
        raise PluginError(name, exc) from exc
    except Exception as exc:
        raise PluginError(name, exc) from exc

    _loaded.add(name)
    plugin = manifest_of(found.module)
    if plugin is None:
        return None  # it registered itself on import
    try:
        plugin.register_all()
    except Exception as exc:
        _loaded.discard(name)
        raise PluginError(name, exc) from exc
    return plugin


@dataclass
class LoadReport:
    """What `load_all` managed, and what it could not."""

    loaded: list[str] = field(default_factory=list)
    #: Installed plugins that failed, as (name, message). NOT fatal -- see
    #: `load_all`.
    failed: list[tuple[str, str]] = field(default_factory=list)

    def __iter__(self):
        """Iterating yields the loaded names, as the old return value did."""
        return iter(self.loaded)

    def __len__(self) -> int:
        return len(self.loaded)


def load_all(
    names: list[str] | tuple[str, ...] = (), *, include_installed: bool = True
) -> LoadReport:
    """Load the installed set, then `names`, the environment, entry points.

    A plugin named EXPLICITLY -- `--plugin x` -- raises if it fails, because
    the user just asked for it by name and continuing without it produces
    `unknown backend` for a backend they are looking at.

    An INSTALLED plugin that fails is reported and skipped. It was named on
    some previous day, and making it fatal means a plugin that breaks (its
    package uninstalled, its file deleted) takes the whole compiler with it --
    including `asmpython plugin remove`, the one command that would fix it.
    """
    report = LoadReport()

    def _one(name: str, sources: Sources | None = None) -> None:
        before = len(_loaded)
        load(name, sources)
        if len(_loaded) != before:
            report.loaded.append(name)

    if include_installed:
        for entry in store.read():
            # Resolved the way it was added: a plugin installed from the
            # working directory must not later resolve to a same-named
            # package from an index.
            try:
                before = len(_loaded)
                load_installed(entry)
                if len(_loaded) != before:
                    report.loaded.append(entry.name)
            except PluginError as exc:
                report.failed.append((entry.name, str(exc.cause)))
    for name in names:
        _one(name)
    for name in _split_env(os.environ.get(ENV_VAR, "")):
        _one(name)
    for name in _entry_point_modules():
        _one(name)
    return report


# ── install / uninstall ───────────────────────────────────────────────────


class AlreadyInstalled(Exception):
    """`install` was called for a name that is already installed.

    Raised rather than silently replacing, because replacing CLEARS a cached
    plugin the user may have edited or may no longer have the source for. The
    caller decides -- the CLI asks -- and passes `replace=True` once the
    answer is yes.
    """

    def __init__(self, entry: "store.Entry") -> None:
        super().__init__(f"plugin {entry.name!r} is already installed")
        self.entry = entry


def cache_plugin(name: str, found: Resolution) -> Path:
    """Copy a resolved plugin into the cache; return the path to import.

    An install loads from the CACHE, not from where it was found, so it keeps
    working when the original moves, is edited mid-build, or lives in a
    directory the compiler is no longer run from. `origin` stays in the store
    for exactly one purpose -- `invalidate` goes back to it deliberately --
    and nothing else re-reads it.

    A package (`mypack/__init__.py`) is copied whole, a single module as one
    file, both under `<cache>/<name>/` so removal is one rmtree and cannot
    strand half a package.
    """
    import shutil

    origin = Path(found.origin) if found.origin else None
    target = store.plugin_cache(name)
    if origin is None or not origin.exists():
        # A namespace package or C extension has no single file to copy.
        # Nothing is cached and the loader falls back to importing by name --
        # exactly what happened before caching existed.
        return Path()
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    if origin.name == "__init__.py":
        shutil.copytree(origin.parent, target / name, dirs_exist_ok=True)
        return target / name / "__init__.py"
    dest = target / origin.name
    shutil.copy2(origin, dest)
    return dest


def _load_cached(name: str, cached: str):
    """Import a plugin from its cached copy, by path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, cached)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load cached plugin from {cached}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so a package's own submodule imports resolve
    # through sys.modules rather than re-executing the module.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def load_installed(entry: store.Entry) -> Plugin | None:
    """Load one installed plugin, preferring its cached copy."""
    if entry.name in _loaded:
        return None
    if entry.cached and Path(entry.cached).exists():
        try:
            module = _load_cached(entry.name, entry.cached)
        except Exception as exc:                   # noqa: BLE001 -- reported
            raise PluginError(entry.name, exc) from exc
        _loaded.add(entry.name)
        plugin = manifest_of(module)
        if plugin is not None:
            try:
                plugin.register_all()
            except Exception as exc:               # noqa: BLE001 -- reported
                _loaded.discard(entry.name)
                raise PluginError(entry.name, exc) from exc
        return plugin
    # No cache -- an older install, or a plugin with no single file to copy.
    # Resolve it the way it was added.
    return load(entry.name, entry.sources)


def invalidate(names: list[str] | tuple[str, ...]) -> list[store.Entry]:
    """Re-resolve each named plugin from its ORIGIN and refresh its cache.

    The one operation that goes back to where a plugin came from. It is how an
    edited plugin under development is picked up, and it fails loudly when the
    origin is gone -- which is the useful answer, because a cached copy that no
    longer matches any real source is exactly the state worth being told about.
    """
    refreshed: list[store.Entry] = []
    for name in names:
        entry = store.find(name)
        if entry is None:
            raise PluginError(name, LookupError("not installed"))
        # Re-resolving has to read DISK, and a plugin loaded earlier this
        # process is already in sys.modules under its own name -- so an
        # ordinary import would hand back the cached copy and report success
        # for a source that no longer exists. Drop both records first.
        sys.modules.pop(name, None)
        _loaded.discard(name)
        try:
            found = resolve(name, entry.sources)
        except Exception as exc:                   # noqa: BLE001 -- reported
            raise PluginError(name, exc) from exc
        try:
            cached = cache_plugin(name, found)
        except OSError as exc:
            # The module resolved but its file could not be copied: it was
            # deleted or moved between the two steps, or is unreadable. That
            # is the same failure as "no longer valid" and reads better said
            # that way than as a copy error.
            raise PluginError(
                name, FileNotFoundError(
                    f"resolved to {found.origin or '?'}, which cannot be "
                    f"read back")) from exc
        plugin = manifest_of(found.module)
        entry.origin = found.origin
        entry.source = found.source
        entry.cached = "" if cached == Path() else str(cached)
        entry.provides = plugin.contents() if plugin is not None else {}
        store.add(entry)
        refreshed.append(entry)
    return refreshed


def install(
    name: str, sources: Sources | None = None, root: Path | None = None,
    *, replace: bool = False,
) -> store.Entry:
    """Resolve, load, and remember. What `asmpython plugin add` does.

    The plugin is LOADED before it is recorded. Writing the name down first
    would let a broken plugin be installed, and then every later invocation
    of asmpython -- including `plugin remove` -- would fail on loading it.
    """
    sources = sources or Sources()
    existing = store.find(name)
    if existing is not None and not replace:
        # The caller has to say so. Replacing clears a cached copy that may be
        # the only remaining copy -- the origin it was installed from is not
        # guaranteed to still exist.
        raise AlreadyInstalled(existing)
    try:
        found = resolve(name, sources, root)
    except Exception as exc:
        # Every failure, not just ResolveError. A module that RAISES while
        # importing is the common case for a plugin under development, and
        # letting that escape prints a compiler traceback for a bug in the
        # user's own file -- which reads as "you found a bug in asmpython".
        raise PluginError(name, exc) from exc

    plugin = manifest_of(found.module)
    provides: dict[str, list[str]] = {}
    try:
        if plugin is not None:
            provides = plugin.contents()
            if name not in _loaded:
                plugin.register_all()
        _loaded.add(name)
    except Exception as exc:
        _loaded.discard(name)
        raise PluginError(name, exc) from exc

    # Clear the old cache only once the new plugin has RESOLVED and
    # REGISTERED. Doing it earlier would leave a name installed with no
    # loadable copy whenever the replacement turned out to be broken.
    if existing is not None:
        store.clear_cache(name)
    cached = cache_plugin(name, found)

    entry = store.Entry(
        name=name,
        sources=sources,
        origin=found.origin,
        source=found.source,
        cached="" if cached == Path() else str(cached),
        provides=provides,
    )
    store.add(entry)
    return entry


def uninstall(name: str) -> bool:
    """Forget a plugin. True if it was installed."""
    store.clear_cache(name)
    return store.remove(name)


def installed() -> list[store.Entry]:
    return store.read()


# ── helpers ───────────────────────────────────────────────────────────────


def _split_env(value: str) -> list[str]:
    """Split on either separator.

    `os.pathsep` is `;` on Windows and `:` elsewhere, and a value written on
    one platform routinely runs on the other -- in a container, in CI. Both
    are accepted rather than making that a portability trap.
    """
    return [c.strip() for c in value.replace(";", ":").split(":") if c.strip()]


def _entry_point_modules() -> list[str]:
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return []
    try:
        found = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001
        # Broken metadata in some unrelated distribution must not stop the
        # compiler from running with its built-ins. This is the one failure
        # here that is neither the user's plugin nor anything they can fix.
        return []
    # `value` is `module:attr` or just `module`; importing the module is what
    # registers, so the attribute is irrelevant here.
    return [ep.value.split(":")[0].strip() for ep in found]
