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


class Backend:
    """Registers a codegen backend, selectable via `--backend name`.

    `impl` must conform to `asmpython._compiler.ir.IRBackend`:
    `compile(module, args) -> dict[str, bytes]` and
    `link(objects, args) -> dict[str, bytes]` are required;
    `requested_args` (list[dict]) and `default_linker` (str) are optional.

    Registration happens immediately, as a side effect of construction.
    """

    def __init__(self, name: str, impl: object) -> None:
        self.name = name
        self.impl = impl
        _lazy_backends_module().register_backend(name, impl)

    def __repr__(self) -> str:
        return f"Backend(name={self.name!r})"
