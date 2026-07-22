"""Public API for registering third-party codegen backends.

    import asmpython

    backend = asmpython.backend
    backend.Backend(name="my_backend", impl=my_backend_impl)

Then: `asmpython build myfile.py --backend my_backend`.
"""

from __future__ import annotations



def _lazy_backends_module():
    # Imported lazily (not at module load time) so `import asmpython` alone
    # doesn't pull in the whole compiler frontend.
    from . import _backends as _backends_pkg

    return _backends_pkg


class _ConfiguredBackend:
    """Inject shared build options without changing third-party implementations."""

    def __init__(self, impl: object, production_suitable: bool) -> None:
        self._impl = impl
        self.production_suitable = bool(production_suitable)

    def __getattr__(self, name: str):
        return getattr(self._impl, name)

    @property
    def requested_args(self) -> list[dict]:
        return getattr(self._impl, "requested_args", [])

    @property
    def default_linker(self) -> str:
        return getattr(self._impl, "default_linker", "gcc")

    def compile(self, module: object, args: dict) -> dict[str, bytes]:
        from ._compiler.build_options import inject_build_options

        return self._impl.compile(module, inject_build_options(args))

    def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
        from ._compiler.build_options import inject_build_options

        return self._impl.link(objects, inject_build_options(args))


class Backend:
    """Registers a codegen backend, selectable via `--backend name`.

    `impl` must conform to `asmpython._compiler.ir.IRBackend`:
    `compile(module, args) -> dict[str, bytes]` and
    `link(objects, args) -> dict[str, bytes]` are required;
    `requested_args` (list[dict]) and `default_linker` (str) are optional.

    Every compile/link call receives ``args["speedy_lossy"]``. Set
    ``production_suitable=False`` for preview, research, or intentionally
    incomplete implementations that must not be used for production claims.

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
        self.impl = _ConfiguredBackend(impl, production_suitable)
        self.production_suitable = bool(production_suitable)
        _lazy_backends_module().register_backend(name, self.impl)

    def __repr__(self) -> str:
        return (
            f"Backend(name={self.name!r}, "
            f"production_suitable={self.production_suitable!r})"
        )
