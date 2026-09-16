"""Backend families, so the word size is a CHOICE rather than a name.

WHAT A FAMILY IS. `x86` and `arm` are not code generators; they are the two
architectures, each of which has a 32-bit and a 64-bit code generator. A user
who wants ARM wants ARM, and whether that is AArch64 or ARMv7 is a property of
the machine they are aiming at rather than a different compiler to invoke.

WHY IT IS RESOLVED HERE AND NOT REGISTERED. A family cannot emit anything, so
registering one as a `Backend` would put a name in the registry that answers
`emit` with a dispatch -- and every list of backends would then show five
entries for four code generators. Resolving the name before the registry is
consulted keeps the registry a list of things that actually compile.

WHERE THE WORD SIZE COMES FROM, in order:

  1. `--bits`, when given. An explicit choice wins and is checked against
     everything else rather than silently overriding it.
  2. The target's `pointer_bits`, when a target is named. `--target
     aarch64-linux` has already said 64; making the user say it twice would
     be a second place for the two to disagree.
  3. Sixty-four. See `DEFAULT_BITS`.
"""
from __future__ import annotations

#: The families and their members. A member NEED NOT BE FINISHED -- `x86-32`
#: is a registered backend that refuses -- because the selection and the
#: implementation are separate questions, and a family that hid its unfinished
#: half would answer "unknown backend" for a name that exists.
FAMILIES: dict[str, dict[int, str]] = {
    "x86": {32: "x86-32", 64: "x86-64"},
    "arm": {32: "arm32", 64: "arm64"},
}

#: The word size assumed when nothing says otherwise.
#:
#: NOT AN ARBITRARY DEFAULT. The IR fixes `ptr` at eight bytes -- see
#: `ir/types.py` -- so 64 is the only width the whole compiler currently
#: agrees on, and a 32-bit selection is a request the backends refuse rather
#: than a path that quietly produces a program. When that changes this default
#: should follow the host, not stay here.
DEFAULT_BITS = 64

#: Which family a concrete backend belongs to, and at what width. Derived so
#: the two directions cannot disagree about a name.
MEMBER_OF: dict[str, tuple[str, int]] = {
    name: (family, bits)
    for family, members in FAMILIES.items()
    for bits, name in members.items()
}


class SelectionError(Exception):
    """A backend, target and word size that cannot all be true at once."""


def resolve_backend(name: str, bits: int | None, target=None) -> str:
    """The concrete backend `name` means, given a word size and a target.

    A NAME THAT IS NOT A FAMILY PASSES THROUGH, so this is safe to call on
    every build rather than only when a family was named -- but it still
    CHECKS an explicit `--bits` against the backend, because `--backend x86-64
    --bits 32` is a request that cannot be honoured and saying so beats
    ignoring half of it.
    """
    wanted = bits if bits is not None else _bits_of(target)
    if name in FAMILIES:
        members = FAMILIES[name]
        if wanted not in members:
            raise SelectionError(
                f"the {name} family has no {wanted}-bit backend "
                f"(it has: {', '.join(str(b) for b in sorted(members))})")
        return members[wanted]
    if bits is not None and name in MEMBER_OF:
        family, native = MEMBER_OF[name]
        if native != bits:
            other = FAMILIES[family][bits] if bits in FAMILIES[family] else None
            hint = (f"; use --backend {other}, or --backend {family} "
                    f"--bits {bits}") if other else ""
            raise SelectionError(
                f"--bits {bits} contradicts --backend {name}, which is "
                f"{native}-bit{hint}")
    return name


def resolve_target(backend: str, bits: int | None, named: str | None,
                   registry) -> str | None:
    """The target name to use, or None to leave the backend's default.

    A NAMED TARGET IS NEVER OVERRIDDEN -- it is only checked. `--bits 32
    --target x86_64-linux` names two different machines, and picking one would
    make the other silently ignored; the point of `--bits` is to choose when
    nothing else has, not to reinterpret what has.
    """
    if named is not None:
        if bits is not None:
            actual = registry.get(named).pointer_bits
            if actual != bits:
                raise SelectionError(
                    f"--bits {bits} contradicts --target {named}, which is "
                    f"{actual}-bit")
        return named
    if bits is None:
        return None
    # NO TARGET AND AN EXPLICIT WIDTH. Take the backend's own default when it
    # already matches, so `--bits 64` changes nothing for a backend that was
    # going to be 64-bit anyway, and only search when it would not.
    from . import base as backend_registry
    default = backend_registry.get(backend).default_target
    try:
        if registry.get(default).pointer_bits == bits:
            return None
    except SystemExit:
        pass
    arch = _ARCH_OF.get(backend)
    matching = sorted(
        name for name in registry.available()
        if registry.get(name).arch == arch
        and registry.get(name).pointer_bits == bits
        and not registry.get(name).is_source)
    if not matching:
        # NO TARGET BECAUSE NO BACKEND. A width whose code generator is not
        # written has no platform registered for it either -- see the
        # `x86_32` and `arm32` docstrings, which say a target naming a
        # platform nothing can compile for is its own failure. Reporting the
        # missing target would send the user to `uasm targets` to look
        # for something that is deliberately absent, so the backend answers
        # instead, and says what it is waiting on.
        from . import base as backend_registry
        be = backend_registry.get(backend)
        if not be.ready:
            try:
                be.emit(None, None)                     # type: ignore[arg-type]
            except Exception as exc:
                raise SelectionError(
                    f"--bits {bits} selects the {backend} backend, and "
                    f"{exc}") from None
        raise SelectionError(
            f"no {bits}-bit target is registered for the {backend} backend; "
            f"name one with --target, or see `uasm targets`")
    return matching[0]


#: The architecture each machine backend emits for, for matching a target to
#: a word size. Kept beside the families because it answers the same question.
_ARCH_OF = {"x86-64": "x86_64", "x86-32": "x86",
            "arm64": "aarch64", "arm32": "arm"}


def _bits_of(target) -> int:
    if target is None:
        return DEFAULT_BITS
    return getattr(target, "pointer_bits", DEFAULT_BITS)
