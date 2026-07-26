"""Public API for registering third-party source-language frontends.

    import asmpython

    asmpython.frontend.Frontend(
        name="lua",
        impl=my_lua_frontend,          # an IRFrontend (has .parse(src, ctx))
        capabilities=asmpython.CapabilitySet(...),
        dependencies=(asmpython.Dependency.executable("luac"),),
    )

Then: `asmpython build myfile.lua --frontend lua`.

A frontend is the mirror image of a backend (see :mod:`asmpython.backend`):
where a backend turns the shared IR into artifacts, a frontend turns source
text of some language into the typed module the IR pipeline consumes. See
``asmpython._compiler.ir.IRFrontend`` for the interface an ``impl`` implements.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

from .capabilities import CapabilitySet, Dependency


def _lazy_frontends_module():
    from . import _frontends as _frontends_pkg
    return _frontends_pkg


class _ConfiguredFrontend:
    """Expose a normalized compatibility contract around a frontend impl."""

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

    @property
    def source_extensions(self) -> tuple[str, ...]:
        return tuple(getattr(self._impl, "source_extensions", ()))

    def parse(self, src: str, ctx: object) -> object:
        from ._compiler.build_report import stage

        with stage("frontend.parse", frontend=self.name):
            return self._impl.parse(src, ctx)


class Frontend:
    """Registers a source-language frontend selectable via ``--frontend name``.

    ``capabilities`` and ``dependencies`` are used before compilation to reject
    impossible source/toolchain combinations. Existing plugins may omit them;
    explicit contracts are strongly recommended for production extensions.
    """

    def __init__(
        self,
        name: str,
        impl: object,
        *,
        production_suitable: bool | None = None,
        capabilities: CapabilitySet | Mapping[str, Any] | None = None,
        dependencies: Iterable[Dependency | Mapping[str, Any] | str] = (),
        aliases: Iterable[str] = (),
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
        self._registered_impl = _ConfiguredFrontend(
            name,
            impl,
            self.production_suitable,
            normalized,
        )
        _lazy_frontends_module().register_frontend(
            name, self._registered_impl, aliases=tuple(aliases)
        )

    def __repr__(self) -> str:
        return (
            f"Frontend(name={self.name!r}, "
            f"production_suitable={self.production_suitable!r}, "
            f"capability_api={self.capabilities.api_version!r})"
        )


__all__ = ["Frontend"]
