"""Public descriptor and registry for ASMPython extension packages.

An extension entry module normally contains:

    from asmpython import Extension

    extension = Extension(id="my_extension")

Creating the object registers its identity in the current host process. The
extension package manager validates that the object named by ``apext.json`` is
an :class:`Extension` and that its id matches the archive manifest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


_EXTENSION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REGISTRY: dict[str, "Extension"] = {}


@dataclass
class Extension:
    """Describes one installable ASMPython extension.

    Extension code may register backends, linkers, mlang configurations, or
    other hooks as normal import-time side effects. ``on_load`` and
    ``on_unload`` are optional lifecycle callbacks for work that should happen
    only when the extension manager activates or deactivates the package.
    """

    id: str
    version: str = "0.0.0"
    description: str = ""
    api_version: int = 1
    production_suitable: bool = True
    on_load: Callable[[Any], Any] | None = None
    on_unload: Callable[[Any], Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _active: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _EXTENSION_ID.fullmatch(self.id):
            raise ValueError(
                "extension id must use lowercase letters, digits, '.', '_' or '-' "
                "and begin with a letter or digit"
            )
        if not isinstance(self.api_version, int) or self.api_version < 1:
            raise ValueError("extension api_version must be a positive integer")
        previous = _REGISTRY.get(self.id)
        if previous is not None and previous is not self:
            raise ValueError(f"extension id {self.id!r} is already registered")
        _REGISTRY[self.id] = self

    def activate(self, asmpython_module: Any | None = None) -> None:
        """Activate the extension exactly once in this process."""

        if self._active:
            return
        if asmpython_module is None:
            import asmpython as asmpython_module
        if self.on_load is not None:
            self.on_load(asmpython_module)
        self._active = True

    def deactivate(self, asmpython_module: Any | None = None) -> None:
        """Run the optional unload hook and mark the extension inactive."""

        if not self._active:
            return
        if asmpython_module is None:
            import asmpython as asmpython_module
        if self.on_unload is not None:
            self.on_unload(asmpython_module)
        self._active = False


def get_extension(extension_id: str) -> Extension | None:
    return _REGISTRY.get(extension_id)


def registered_extensions() -> dict[str, Extension]:
    return dict(_REGISTRY)


__all__ = ["Extension", "get_extension", "registered_extensions"]
