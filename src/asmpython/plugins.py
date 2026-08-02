"""Getting third-party registrations to happen.

The four registries have always accepted a third party's backend, target,
toolchain or frontend: `register()` is the same call the built-ins make, on
purpose, so that no extension path is one the built-ins bypass.

WHAT WAS MISSING WAS REACHABILITY. Registration happens as a side effect of
importing a module, and nothing ever imported anyone else's. Embedding
asmpython as a library worked -- you import your module yourself -- but from
the command line a third-party backend could register perfectly and then be
reported as `unknown backend`, because `asmpython build` only ever called
`load_builtin()`. The extension point existed and could not be used.

Two ways in, because they answer different questions:

  * `--plugin mypack` names a module to import. For trying something out, for
    a backend that lives next to the program it compiles, and for anyone who
    has not packaged anything yet.
  * An `asmpython.plugins` ENTRY POINT is declared by an installed
    distribution and needs no flag. For a backend someone `pip install`s.

Both end in `import`, and the import is what registers. Nothing here knows
what a plugin contains: a module registering three backends and a target is
the same to this file as one registering nothing.

FAILURES ARE REPORTED, NOT SWALLOWED. A plugin that raises on import is a
broken plugin, and the alternative -- carrying on with it silently missing --
produces `unknown backend 'counting'` for a backend the user can see in their
own source file.
"""
from __future__ import annotations

import importlib
import os

#: Distributions advertise plugins under this entry-point group.
ENTRY_POINT_GROUP = "asmpython.plugins"

#: A colon/semicolon-separated list of modules to import, for environments
#: where passing a flag is awkward -- a test harness, a CI job, a Makefile
#: that already sets the environment for everything else.
ENV_VAR = "ASMPYTHON_PLUGINS"

#: Modules already imported by this process, so `--plugin x --plugin x` and a
#: module that is also an entry point do not register twice. Registries reject
#: duplicate backend names, so this is load-bearing rather than tidiness.
_loaded: set[str] = set()


class PluginError(Exception):
    """A plugin was named and could not be loaded."""

    def __init__(self, name: str, cause: BaseException) -> None:
        super().__init__(f"plugin {name!r} failed to load: {cause}")
        self.name = name
        self.cause = cause


def load(name: str) -> None:
    """Import one module by name, so its `register()` calls run."""
    if name in _loaded:
        return
    try:
        importlib.import_module(name)
    except Exception as exc:                      # noqa: BLE001 -- reported
        raise PluginError(name, exc) from exc
    _loaded.add(name)


def load_all(names: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Load `names`, plus the environment variable, plus every entry point.

    Returns what was loaded, in the order it happened, so `--verbose` can say
    -- a plugin that silently does nothing is indistinguishable from one that
    never loaded, and that is the first question anyone asks.
    """
    loaded: list[str] = []

    def _one(name: str) -> None:
        before = len(_loaded)
        load(name)
        if len(_loaded) != before:
            loaded.append(name)

    for name in names:
        _one(name)
    for name in _split_env(os.environ.get(ENV_VAR, "")):
        _one(name)
    for name in _entry_point_modules():
        _one(name)
    return loaded


def _split_env(value: str) -> list[str]:
    """Split on either separator.

    `os.pathsep` is `;` on Windows and `:` elsewhere, and a value written on
    one platform routinely runs on the other -- in a container, in CI. Both
    are accepted rather than making that a portability trap.
    """
    parts: list[str] = []
    for chunk in value.replace(";", ":").split(":"):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def _entry_point_modules() -> list[str]:
    """Module names advertised under the `asmpython.plugins` group."""
    try:
        from importlib.metadata import entry_points
    except ImportError:                            # pragma: no cover
        return []
    try:
        found = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:                              # noqa: BLE001
        # A broken distribution's metadata must not stop the compiler from
        # running with the built-ins. This is the one failure here that is
        # not the user's plugin and not something they can fix.
        return []
    names = []
    for ep in found:
        # `value` is `module:attr` or just `module`; importing the module is
        # what registers, so the attribute is irrelevant to us.
        names.append(ep.value.split(":")[0].strip())
    return names
