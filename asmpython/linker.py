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


class Linker:
    """Registers a linker, selectable via `--linker name`.

    `impl` must expose `link(ctx: dict) -> bytes`; `requested_args`
    (list[dict]) is optional. `ctx` carries at minimum `objects` (a list of
    object-file bytes to link) and `target_os`.

    Registration happens immediately, as a side effect of construction.
    """

    def __init__(self, name: str, impl: object) -> None:
        self.name = name
        self.impl = impl
        _lazy_linkers_module().register_linker(name, impl)

    def __repr__(self) -> str:
        return f"Linker(name={self.name!r})"
