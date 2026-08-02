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
                _one(entry.name, entry.sources)
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


def install(
    name: str, sources: Sources | None = None, root: Path | None = None
) -> store.Entry:
    """Resolve, load, and remember. What `asmpython plugin add` does.

    The plugin is LOADED before it is recorded. Writing the name down first
    would let a broken plugin be installed, and then every later invocation
    of asmpython -- including `plugin remove` -- would fail on loading it.
    """
    sources = sources or Sources()
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

    entry = store.Entry(
        name=name,
        sources=sources,
        origin=found.origin,
        source=found.source,
        provides=provides,
    )
    store.add(entry)
    return entry


def uninstall(name: str) -> bool:
    """Forget a plugin. True if it was installed."""
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
