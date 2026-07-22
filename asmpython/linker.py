"""Public API for registering third-party linkers.

    import asmpython

    asmpython.linker.Linker(
        name="my_linker",
        impl=my_linker_impl,
        capabilities=asmpython.CapabilitySet(...),
        dependencies=(asmpython.Dependency.executable("ld"),),
    )
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

from .capabilities import CapabilitySet, Dependency


def _lazy_linkers_module():
    from . import _linkers as _linkers_pkg
    return _linkers_pkg


class _ConfiguredLinker:
    """Inject shared options and expose a normalized compatibility contract."""

    def __init__(
        self,
        name: str,
        impl: object,
        production_suitable: bool,
        capabilities: CapabilitySet,
    ) -> None:
        self.name = name
        self._impl = impl
        self.production_suitable = bool(production_suitable)
        self.capabilities = capabilities
        self.dependencies = capabilities.dependencies

    def __getattr__(self, name: str):
        return getattr(self._impl, name)

    @property
    def requested_args(self) -> list[dict]:
        return getattr(self._impl, "requested_args", [])

    def link(self, ctx: dict) -> bytes:
        from ._compiler.build_options import inject_build_options
        from ._compiler.build_report import event, stage

        resolved = inject_build_options(ctx)
        with stage(
            "linker.link",
            linker=self.name,
            input_objects=len(resolved.get("objects", [])),
        ):
            output = self._impl.link(resolved)
        event("linker.output", linker=self.name, bytes=len(output))
        return output


class Linker:
    """Registers a linker selectable via ``--linker name``.

    ``capabilities`` and ``dependencies`` participate in pre-build negotiation,
    lockfile generation, doctor output, and build reports. Existing plugins may
    omit them, but production linkers should declare an explicit contract.
    """

    def __init__(
        self,
        name: str,
        impl: object,
        *,
        production_suitable: bool | None = None,
        capabilities: CapabilitySet | Mapping[str, Any] | None = None,
        dependencies: Iterable[Dependency | Mapping[str, Any] | str] = (),
    ) -> None:
        if production_suitable is None:
            production_suitable = bool(getattr(impl, "production_suitable", True))
        impl_capabilities = capabilities
        if impl_capabilities is None:
            impl_capabilities = getattr(impl, "capabilities", None)
        combined_dependencies = (
            *tuple(getattr(impl, "dependencies", ())),
            *tuple(dependencies),
        )
        normalized = CapabilitySet.from_value(
            impl_capabilities,
            dependencies=combined_dependencies,
        )
        self.name = name
        self.impl = impl
        self.production_suitable = bool(production_suitable)
        self.capabilities = normalized
        self.dependencies = normalized.dependencies
        self._registered_impl = _ConfiguredLinker(
            name,
            impl,
            self.production_suitable,
            normalized,
        )
        _lazy_linkers_module().register_linker(name, self._registered_impl)

    def __repr__(self) -> str:
        return (
            f"Linker(name={self.name!r}, "
            f"production_suitable={self.production_suitable!r}, "
            f"capability_api={self.capabilities.api_version!r})"
        )
