"""lllib -- asmpython's low-level library.

Bit manipulation, explicit byte order, and raw memory: the operations a
systems program needs and the Python standard library has no reason to offer.

    from asmpython import lllib

    lllib.bits.popcount(0b1011, 32)      # 3
    lllib.bits.rotl(0x80000001, 1, 32)   # 0x00000003
    lllib.endian.unpack(header, 4, 4, lllib.endian.BIG)

Three implementations, picked in this order
-------------------------------------------
1. **The backend's**, when the machine does it in one instruction. x86-64
   emits POPCNT and BSWAP inline; a backend declares what it implements via
   ``lllib_intrinsics`` on its ``__lllib__``. Nothing is registered here and
   nothing is hardcoded, so a third-party backend opts in on the same terms as
   a built-in one.

   LZCNT/TZCNT are deliberately absent: without BMI1/LZCNT their encodings
   decode as BSR/BSF, which differ on a zero input. A wrong answer on one value
   is worse than the loop.
2. **The APC implementation**, in ``_frontends/apc/std/bits.apc``. It also
   publishes the reserved intrinsic symbols, so a backend that does not
   recognize them links against it instead -- same symbol, one rung down.
3. **The Python implementation** in this package, which works everywhere,
   including on backends that only emit bytecode.

Every layer is written against the same signatures and tested against the same
cases, so which one answers is a performance question and never a correctness
one. ``implementation_of("popcount")`` says which you got.

Measured on 2M popcounts, compiled at ``--passes o2``:

    called loop (APC)     5085 ms
    called loop (Python)  5522 ms
    POPCNT inline          128 ms

which is why the layering exists at all: the portable rungs are within 8% of
each other, and the instruction is 40x faster than both.

Why not per-backend modules only
--------------------------------
``asmpython.lllib.<backend>`` exists too, for what genuinely cannot be
portable. But the portable surface has to be the default one, because the
alternative is that every caller names an architecture and no portable
low-level code can be written at all. That split -- shared by default, per-arch
where the difference is real -- is the one ``_backends/_common/`` arrived at
after finding two duplicated analyses that had each silently diverged.
"""

from __future__ import annotations

import importlib
from typing import Any

from . import bits, endian  # noqa: F401  (the portable implementations)

#: Backends checked for a ``__lllib__`` attribute. Being listed is not a claim
#: that a backend exposes anything -- one that does not is simply absent from
#: :func:`backends`.
_CANDIDATES: tuple[str, ...] = (
    "x86_64", "x86", "arm64", "arm", "thumb", "riscv", "mips",
    "powerpc", "jvm", "android", "webassembly", "ternary",
)

_cache: dict[str, Any] = {}


def _load(name: str) -> Any:
    if name in _cache:
        return _cache[name]
    try:
        module = importlib.import_module(f".._backends.{name}", __name__)
    except Exception:
        # A backend that cannot import -- a missing optional dependency, a
        # scaffold that raises on load -- has no low-level surface. That is an
        # absence, not an error.
        _cache[name] = None
        return None
    _cache[name] = getattr(module, "__lllib__", None)
    return _cache[name]


def backends() -> "list[str]":
    """Names of backends publishing a low-level surface."""
    return [name for name in _CANDIDATES if _load(name) is not None]


def for_backend(name: str) -> Any:
    """The low-level surface of ``name``.

    ``AttributeError`` -- not ``ImportError`` -- when the backend exists but
    publishes nothing, so the two cases stay distinguishable.
    """
    surface = _load(name)
    if surface is None:
        raise AttributeError(
            f"backend {name!r} exposes no low-level surface "
            f"(available: {', '.join(backends()) or 'none'})"
        )
    return surface


def implementation_of(operation: str, backend: str | None = None) -> str:
    """Which layer answers `operation`: "backend:<name>", "apc", or "python".

    For checking that a build got the implementation it expected, rather than
    silently falling back to the portable one in a hot loop.
    """
    for name in ([backend] if backend else backends()):
        surface = _load(name)
        if operation in getattr(surface, "lllib_intrinsics", ()):
            return f"backend:{name}"
    if hasattr(bits, operation):
        return "python"
    raise AttributeError(f"lllib has no operation {operation!r}")


def __getattr__(name: str) -> Any:
    """Resolve ``asmpython.lllib.<backend>`` lazily.

    Lazy so importing lllib for the portable surface never drags in every
    backend's encoder, and a backend whose optional dependencies are missing
    costs nothing until asked for by name.
    """
    if not name.startswith("_"):
        surface = _load(name)
        if surface is not None:
            return surface
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> "list[str]":
    return sorted({*globals(), *backends()})


__all__ = ["backends", "bits", "endian", "for_backend", "implementation_of"]
