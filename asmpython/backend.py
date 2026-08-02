"""Public API for registering third-party codegen backends.

    import asmpython

    asmpython.backend.Backend(
        name="my_backend",
        impl=my_backend_impl,
        capabilities=asmpython.CapabilitySet(...),
        dependencies=(asmpython.Dependency.executable("my-codegen"),),
    )

Then: `asmpython build myfile.py --backend my_backend`.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

from .capabilities import CapabilitySet, Dependency


def _lazy_backends_module():
    from . import _backends as _backends_pkg
    return _backends_pkg


class _ConfiguredBackend:
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

    @property
    def default_linker(self) -> str:
        return getattr(self._impl, "default_linker", "gcc")

    def compile(self, module: object, args: dict) -> dict[str, bytes]:
        from ._compiler.build.build_options import inject_build_options
        from ._compiler.build.build_report import event, stage

        resolved = inject_build_options(args)
        with stage("backend.compile", backend=self.name):
            outputs = self._impl.compile(module, resolved)
        event(
            "backend.outputs",
            backend=self.name,
            phase="compile",
            outputs={name: len(data) for name, data in outputs.items()},
        )
        return outputs

    def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
        from ._compiler.build.build_options import inject_build_options
        from ._compiler.build.build_report import event, stage

        resolved = inject_build_options(args)
        with stage("backend.link", backend=self.name, input_objects=len(objects)):
            outputs = self._impl.link(objects, resolved)
        event(
            "backend.outputs",
            backend=self.name,
            phase="link",
            outputs={name: len(data) for name, data in outputs.items()},
        )
        return outputs


class Backend:
    """Registers a codegen backend selectable via ``--backend name``.

    Every compile/link call receives ``speedy_lossy``, ``bleach``, and the
    normalized ``sanitizers`` tuple. ``capabilities`` and ``dependencies`` are
    used before compilation to reject impossible target/output/toolchain
    combinations. Existing plugins may omit them; explicit contracts are strongly
    recommended for production extensions.
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
        self._registered_impl = _ConfiguredBackend(
            name,
            impl,
            self.production_suitable,
            normalized,
        )
        _lazy_backends_module().register_backend(name, self._registered_impl)

    def __repr__(self) -> str:
        return (
            f"Backend(name={self.name!r}, "
            f"production_suitable={self.production_suitable!r}, "
            f"capability_api={self.capabilities.api_version!r})"
        )
