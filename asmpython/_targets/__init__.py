"""Registry for target platforms.

A target owns everything that changes when the same IR is emitted for a
different operating system or word size: object layout, entry point, syscall
convention, runtime startup. It is NOT part of the compiler -- the compiler
lowers a program to IR and asks this registry which target to hand it to, and
adding a platform therefore means adding a module here rather than editing the
compiler.

That separation is the point. These modules previously sat inside
`_compiler/`, where `driver.py` imported three of them by name; a fourth
platform meant a fourth import in the compiler, and the compiler grew a list of
every platform anyone had ever wanted.

Built-ins register lazily: importing this package does not import seven
targets, because a Windows build has no reason to load the freestanding
16-bit lowering. Third parties register through `asmpython.target.Target(...)`,
exactly as they do for backends and linkers.
"""
from __future__ import annotations

import importlib
from collections.abc import Iterable
from typing import Any

#: canonical name -> (module, attribute) or an already-registered object.
_REGISTRY: dict[str, Any] = {}
_ALIASES: dict[str, str] = {}

#: The targets that ship with asmpython, as (module, class) so importing this
#: package stays cheap. A real object may replace any of these by name.
_BUILTINS: dict[str, tuple[str, str]] = {
    "windows": (".target_windows", "WindowsCodegen"),
    "linux": (".target_linux", "LinuxCodegen"),
    "freestanding": (".target_freestanding", "FreestandingCodegen"),
    "freestanding16": (".target_freestanding16", "Freestanding16Codegen"),
    "x86_32_linux": (".target_x86_32_linux", "X86_32LinuxCodegen"),
}

_BUILTIN_ALIASES = {
    "win": "windows", "win64": "windows",
    "elf": "linux", "bare": "freestanding", "bare16": "freestanding16",
    "x86-32-linux": "x86_32_linux", "linux32": "x86_32_linux",
}


def register_target(name: str, target: Any, *, aliases: Iterable[str] = ()) -> None:
    """Register `target` under a canonical name and optional aliases.

    Re-registering a canonical name replaces the previous entry, so a
    third-party target can take over a built-in name without registry surgery.
    """
    if not name:
        raise ValueError("target name must not be empty")
    _REGISTRY[name] = target
    for alias in aliases:
        _ALIASES[alias] = name


def resolve(name: str) -> str:
    """Canonical name for `name`, following one level of alias."""
    return _ALIASES.get(name, _BUILTIN_ALIASES.get(name, name))


def get_target(name: str) -> Any:
    """The codegen class registered for `name`.

    Built-ins are imported on first use. An unknown name lists what is
    available rather than raising KeyError, since the usual cause is a typo or
    a plugin that failed to register.
    """
    canonical = resolve(name)
    if canonical in _REGISTRY:
        return _REGISTRY[canonical]
    spec = _BUILTINS.get(canonical)
    if spec is None:
        raise LookupError(
            f"unknown target {name!r}; available: {', '.join(available())}"
        )
    module_name, attribute = spec
    target = getattr(importlib.import_module(module_name, __name__), attribute)
    _REGISTRY[canonical] = target
    return target


def available() -> list[str]:
    return sorted(set(_BUILTINS) | set(_REGISTRY))


def aliases() -> dict[str, str]:
    return {**_BUILTIN_ALIASES, **_ALIASES}
