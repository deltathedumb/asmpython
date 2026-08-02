"""Public API for registering third-party compiler passes.

    import asmpython
    from asmpython._compiler.ssa.ir import IRPass

    class StrengthReduce(IRPass):
        name = "strength-reduce"
        description = "replace imul by a power of two with a shift"
        requires = frozenset({"ssa"})       # needs mem2reg to have run
        preserves = frozenset({"cfg", "ssa"})

        def run(self, module):
            changed = False
            ...                               # transform module in place
            return changed

    asmpython.compiler_pass.CompilerPass("strength-reduce", StrengthReduce())

Then: `asmpython build myfile.py --passes mem2reg,strength-reduce`, or load the
plugin file directly with `--passes ./my_pass.py`.

A pass is a neutral IR->IR transform: it must work regardless of which frontend
produced the module (see ``_compiler/ir_contract.md``). ``requires``/
``provides``/``preserves`` let the pass manager reject an impossible ordering --
e.g. a pass requiring ``"ssa"`` placed before ``mem2reg``.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

from .capabilities import CapabilitySet, Dependency


def _lazy_passes_module():
    from . import _passes as _passes_pkg
    return _passes_pkg


class _ConfiguredPass:
    """Expose a normalized contract around a pass implementation."""

    def __init__(self, name: str, impl: object, capabilities: CapabilitySet) -> None:
        self.name = name
        self._impl = impl
        self.capabilities = capabilities
        self.dependencies = capabilities.dependencies

    def __getattr__(self, name: str):
        return getattr(self._impl, name)

    @property
    def description(self) -> str:
        return str(getattr(self._impl, "description", "") or "")

    @property
    def requires(self) -> frozenset[str]:
        return frozenset(getattr(self._impl, "requires", ()) or ())

    @property
    def provides(self) -> frozenset[str]:
        return frozenset(getattr(self._impl, "provides", ()) or ())

    @property
    def preserves(self) -> frozenset[str]:
        return frozenset(getattr(self._impl, "preserves", ()) or ())

    def run(self, module: object) -> bool:
        from ._compiler.build.build_report import stage

        with stage("pass.run", pass_name=self.name):
            return bool(self._impl.run(module))


class CompilerPass:
    """Registers an IR pass selectable via ``--passes name``.

    Mirrors :class:`asmpython.backend.Backend` and
    :class:`asmpython.frontend.Frontend`; registering a canonical name that
    already exists deliberately replaces it, so a plugin may override a
    built-in pass.
    """

    def __init__(
        self,
        name: str,
        impl: object,
        *,
        capabilities: CapabilitySet | Mapping[str, Any] | None = None,
        dependencies: Iterable[Dependency | Mapping[str, Any] | str] = (),
        aliases: Iterable[str] = (),
    ) -> None:
        impl_capabilities = capabilities
        if impl_capabilities is None:
            impl_capabilities = getattr(impl, "capabilities", None)
        combined_dependencies = (
            *tuple(getattr(impl, "dependencies", ())),
            *tuple(dependencies),
        )
        normalized = CapabilitySet.from_value(
            impl_capabilities, dependencies=combined_dependencies
        )
        self.name = name
        self.impl = impl
        self.capabilities = normalized
        self.dependencies = normalized.dependencies
        self._registered_impl = _ConfiguredPass(name, impl, normalized)
        _lazy_passes_module().register_pass(
            name, self._registered_impl, aliases=tuple(aliases)
        )

    def __repr__(self) -> str:
        return (
            f"CompilerPass(name={self.name!r}, "
            f"capability_api={self.capabilities.api_version!r})"
        )


__all__ = ["CompilerPass"]
