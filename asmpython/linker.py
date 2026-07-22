"""Public API for registering third-party linkers.

    import asmpython

    linker = asmpython.linker
    linker.Linker(name="my_linker", impl=my_linker_impl)

Then: `asmpython build myfile.py --linker my_linker`.
"""

from __future__ import annotations



def _lazy_linkers_module():
    # Imported lazily (not at module load time) so `import asmpython` alone
    # doesn't pull in the whole compiler frontend.
    from . import _linkers as _linkers_pkg

    return _linkers_pkg


class _ConfiguredLinker:
    """Inject shared build options into every third-party linker context."""

    def __init__(self, impl: object, production_suitable: bool) -> None:
        self._impl = impl
        self.production_suitable = bool(production_suitable)

    def __getattr__(self, name: str):
        return getattr(self._impl, name)

    @property
    def requested_args(self) -> list[dict]:
        return getattr(self._impl, "requested_args", [])

    def link(self, ctx: dict) -> bytes:
        from ._compiler.build_options import inject_build_options

        return self._impl.link(inject_build_options(ctx))


class Linker:
    """Registers a linker, selectable via `--linker name`.

    `impl` must expose `link(ctx: dict) -> bytes`; `requested_args`
    (list[dict]) is optional. `ctx` carries at minimum `objects` (a list of
    object-file bytes to link), `target_os`, and the shared
    ``speedy_lossy`` boolean.

    Set ``production_suitable=False`` for a preview, debug, or experimental
    linker that must not be used for production-build claims.

    Registration happens immediately, as a side effect of construction.
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
        self.impl = _ConfiguredLinker(impl, production_suitable)
        self.production_suitable = bool(production_suitable)
        _lazy_linkers_module().register_linker(name, self.impl)

    def __repr__(self) -> str:
        return (
            f"Linker(name={self.name!r}, "
            f"production_suitable={self.production_suitable!r})"
        )
