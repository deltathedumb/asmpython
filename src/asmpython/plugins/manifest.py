"""What a plugin declares.

A plugin is a module holding one `Plugin` object under
`__asmpython_plugin__`:

    from asmpython.plugins import Plugin, Backend, Target, Frontend, Linker

    plugin = Plugin("mypack")
    plugin.backends.append(MyBackend())
    plugin.targets.append(Target("my-machine", arch="my"))

    __asmpython_plugin__ = plugin

DECLARING BEATS DOING. The older way was to call `register()` at import time
and let the side effect be the interface, which works and has one bad
property: you cannot find out what a module provides without letting it
change the compiler's state. `asmpython plugin show` needs exactly that --
so does deciding whether a plugin conflicts with one already installed, and
so does reporting what an install added. A manifest can be read; a side
effect can only be suffered.

Import-time `register()` still works, and the loader supports both. A module
with no `__asmpython_plugin__` is assumed to have registered itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..backend import Backend
from ..frontend import Frontend
from ..link import Toolchain
from ..target import Target
from .patch import CompilerPatch, apply_all

#: `Linker` is what the CLI and the docs call it, `Toolchain` is what the
#: class has always been called. Both names refer to one class rather than
#: one being a wrapper, so an `isinstance` check never has to know which
#: spelling the author used.
Linker = Toolchain

__all__ = ["Plugin", "Backend", "Frontend", "Target", "Toolchain", "Linker",
           "TargetEntry", "CompilerPatch"]


@dataclass(frozen=True, slots=True)
class TargetEntry:
    """A target and the aliases it should answer to."""

    target: Target
    aliases: tuple[str, ...] = ()


@dataclass
class Plugin:
    """Everything one module contributes.

    The four lists are plain lists and are meant to be appended to, because
    that is what the shape of the thing suggests and anything cleverer would
    be a small API to learn for no gain.
    """

    name: str = ""
    version: str = ""
    description: str = ""
    frontends: list[Frontend] = field(default_factory=list)
    backends: list[Backend] = field(default_factory=list)
    targets: list[Target | TargetEntry] = field(default_factory=list)
    linkers: list[Toolchain] = field(default_factory=list)
    #: Direct changes to the compiler, for what the registries do not cover.
    #: See `patch.py` -- two targets are sealed and a few are guarded.
    patches: list[CompilerPatch] = field(default_factory=list)

    def add_target(self, target: Target, *,
                   aliases: Iterable[str] = ()) -> Plugin:
        """Append a target that answers to alternative names.

        `plugin.targets.append(t)` covers the common case; a target with
        aliases needs somewhere to put them, and this is it rather than a
        tuple convention nobody would guess.
        """
        self.targets.append(TargetEntry(target, tuple(aliases)))
        return self

    # ── inspection ────────────────────────────────────────────────────────
    def contents(self) -> dict[str, list[str]]:
        """What this plugin provides, by kind. Reading it registers nothing."""
        return {
            "frontends": [_name_of(f) for f in self.frontends],
            "backends": [_name_of(b) for b in self.backends],
            "targets": [_name_of(_target_of(t)) for t in self.targets],
            "linkers": [_name_of(t) for t in self.linkers],
            "patches": [p.describe() for p in self.patches],
        }

    def is_empty(self) -> bool:
        return not any(self.contents().values())

    # ── the side effect, once, explicitly ─────────────────────────────────
    def register_all(self) -> dict[str, list[str]]:
        """Register everything. Returns what was registered, by kind.

        Order matters: targets first, because a backend names its
        `default_target` and a listing that resolves it would otherwise fail
        on a plugin that ships both.
        """
        from .. import backend as backend_registry
        from .. import frontend as frontend_registry
        from .. import link as link_registry
        from .. import target as target_registry

        done: dict[str, list[str]] = {"targets": [], "backends": [],
                                      "frontends": [], "linkers": [],
                                      "patches": []}
        for entry in self.targets:
            target, aliases = _split_target(entry)
            target_registry.register(target, aliases=aliases)
            done["targets"].append(target.name)
        for be in self.backends:
            backend_registry.register(_instantiate(be))
            done["backends"].append(_name_of(be))
        for fe in self.frontends:
            frontend_registry.register(_instantiate(fe))
            done["frontends"].append(_name_of(fe))
        for tc in self.linkers:
            link_registry.register(_instantiate(tc))
            done["linkers"].append(_name_of(tc))
        # Patches last: they may well be patching something registered above,
        # and a patch applied first would be overwritten by the registration.
        for patch in apply_all(self.patches):
            done["patches"].append(patch.describe())
        return done


def _instantiate(obj: Any) -> Any:
    """Accept a class or an instance.

    `plugin.backends.append(MyBackend)` and `.append(MyBackend())` both read
    as correct, and which one an author writes is a coin flip. Refusing one
    would be a rule to memorise for no benefit.
    """
    return obj() if isinstance(obj, type) else obj


def _target_of(entry: Target | TargetEntry) -> Target:
    return entry.target if isinstance(entry, TargetEntry) else entry


def _split_target(entry: Target | TargetEntry) -> tuple[Target, tuple[str, ...]]:
    if isinstance(entry, TargetEntry):
        return entry.target, entry.aliases
    return entry, ()


def _name_of(obj: Any) -> str:
    name = getattr(obj, "name", "")
    return name or getattr(obj, "__name__", repr(obj))
