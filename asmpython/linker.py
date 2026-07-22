"""Public API for registering third-party linkers.

    import asmpython

    linker = asmpython.linker
    linker.Linker(name="my_linker", impl=my_linker_impl)

Then: `asmpython build myfile.py --linker my_linker`.
"""
from __future__ import annotations


def _lazy_linkers_module():
    from . import _linkers as _linkers_pkg
    return _linkers_pkg


class _ConfiguredLinker:
    """Inject shared build options into every third-party linker context."""

    def __init__(self, name: str, impl: object, production_suitable: bool) -> None:
        self.name = name
        self._impl = impl
        self.production_suitable = bool(production_suitable)

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
    """Registers a linker, selectable via `--linker name`.

    `impl` must expose `link(ctx: dict) -> bytes`; `requested_args`
    (list[dict]) is optional. `ctx` carries at minimum `objects`, `target_os`,
    ``speedy_lossy``, ``bleach``, and ``sanitizers``.

    Set ``production_suitable=False`` for a preview, debug, or experimental
    linker that must not be used for production-build claims.
    """

    def __init__(
        self,
        name: str,
        impl: object,
        *,
        production_suitable: bool | None = None,
    ) -> None:
        if production_suitable is None:
            production_suitable = bool(getattr(impl, "production_suitable", True))
        self.name = name
        self.impl = impl
        self.production_suitable = bool(production_suitable)
        self._registered_impl = _ConfiguredLinker(
            name, impl, self.production_suitable
        )
        _lazy_linkers_module().register_linker(name, self._registered_impl)

    def __repr__(self) -> str:
        return (
            f"Linker(name={self.name!r}, "
            f"production_suitable={self.production_suitable!r})"
        )
