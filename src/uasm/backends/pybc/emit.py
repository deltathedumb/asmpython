"""`pybc` -- CPython bytecode, as a `.pyc`.

THE DESIGN QUESTION THIS BACKEND ANSWERS DIFFERENTLY FROM EVERY OTHER ONE.
Every other backend turns the IR into something the IR does not already know
how to be: C, x86-64, a `.class` file. Doing the same for CPython bytecode
means recovering Python operations out of `call @apy_add`-shaped IR -- the
dynamic half of the Python frontend lowers straight to the object runtime,
and the IR has no tag saying a given call IS a `+`. Building that (a
semantic tag on every object-runtime call, a role on every global, a
code-object writer matching whatever the host CPython's opcode set and
adaptive specialisation happen to be this release) is real, IR-wide work
that touches the frontend, the verifier and every existing backend's
assumptions about what an instruction means -- and it buys a SECOND
bytecode compiler that then has to track CPython's own, release for
release, forever.

THE ESCAPE HATCH IS THAT ONE ALREADY SHIPS WITH EVERY COPY OF UASM.
`pyproject.toml` requires Python 3.14 to even RUN uasm, and for the
same reason the Python frontend parses a user's program with the host's own
`ast`: the target and the host are the same interpreter version by
construction, not by coincidence. A `.pyc` is defined as "whatever the
MAGIC_NUMBER'd host CPython accepts", so the one compiler guaranteed to
produce a correct one is that host's own -- reachable at zero cost, already
imported, and it will not drift from next year's opcode changes because it
IS next year's opcodes. Using it is not a shortcut around this backend; for
an artifact whose whole definition is "what the host interpreter runs", it
is the only implementation that cannot be wrong in the way a hand-rolled
one eventually would be.

WHAT THIS COSTS. This backend does not read `module` -- the verified IR
every other backend compiles -- at all. It recovers the ORIGINAL SOURCE TEXT
from the span any real frontend attaches to nearly every instruction
(`Instruction.span.file.text`; see `diagnostics/span.py`) and hands that
text to the host's own `compile()`. So a `.pyc` built this way reflects the
source as written, not the uasm IR after optimisation passes have run
over it -- `-O` (uasm's pass pipeline) has no effect on this backend's
output; `--opt-level` (this backend's own option, see below) does, because
it is CPython's `-O`/`-OO`, applied by the same host compiler. The program
still has to pass the whole front half of the pipeline first -- parse,
lower, verify -- so building with `--backend pybc` still requires the
source to be within whatever uasm's frontend accepts, same as every
other backend, even though the bytes this backend actually emits never
touch the IR that pipeline produced.

`uasm build prog.py --backend pybc` produces a single `.pyc`, in the
exact format (PEP 552 header + `marshal`) and CPython version the host
that ran the build understands, executable with a plain `python prog.pyc`.
See `tests/uasm/unit/test_pybc_backend.py`.
"""
from __future__ import annotations

import marshal
import struct

from ...backend.base import (
    Backend, BackendUnsupported, Option, OptionError, Target, register,
    source_file_of,
)
from ...ir import Module


class PycBackend(Backend):
    name = "pybc"
    description = "CPython bytecode (.pyc) executable by the host interpreter"
    kind = "binary"
    ready = True
    #: A `.pyc` needs nothing linked in: it runs under the host CPython that
    #: is already there, the same way a `.jar` runs under a JVM that is
    #: already there.
    self_contained = True
    #: Registered in `targets/__init__.py` alongside this backend, once it
    #: could actually target it -- see that file for why the placeholder
    #: was `"c"` before this backend was written.
    default_target = "pybc"
    options = (
        Option("opt-level", metavar="0|1|2", help=(
            "CPython's -O/-OO, applied by the host compiler that builds "
            "this .pyc: 0 keeps asserts and docstrings (the default), 1 "
            "strips asserts, 2 strips asserts and docstrings")),
    )

    #: Set by `configure()`; the default backend instance is never emitted
    #: from directly (the driver always configures first), but a literal 0
    #: here keeps a directly-constructed instance usable in tests.
    _opt_level: int = 0

    def configure(self, values: dict[str, str], sink) -> "PycBackend":
        level = values.get("opt-level", "0")
        if level not in ("0", "1", "2"):
            raise OptionError(
                f"--opt-level wants 0, 1 or 2 (CPython's -O/-OO), got {level!r}")
        be = PycBackend()
        be._opt_level = int(level)
        return be

    def check_host_services(self, module: Module) -> None:
        """Nothing to check: this backend never reads a host-service call.

        Every other backend's `check_host_services` walks `module.functions`
        for external calls it has no implementation for. This one recompiles
        the ORIGINAL SOURCE with the host CPython, which has every host
        service there is (it is a full CPython) -- so a program using
        `open()` or `socket` is exactly as supported here as running it with
        `python` directly, regardless of what `self.host_services` would
        otherwise say.
        """

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        source = source_file_of(module)
        if source is None or not source.text.strip():
            raise BackendUnsupported(
                "the pybc backend recompiles the module's original source "
                "text with the host CPython, and could not recover any: no "
                "instruction, function or global in this module carries a "
                "real source span. An empty module, or IR built by hand "
                "(`--emit-ir` round-tripped back in) rather than compiled "
                "from a .py file, has nothing for this backend to compile.")

        filename = str(source.path) if source.path is not None else source.name
        stem = source.path.stem if source.path is not None else module.name

        try:
            code = compile(source.text, filename, "exec",
                           dont_inherit=True, optimize=self._opt_level)
        except (SyntaxError, ValueError) as exc:
            # THE FRONTEND ALREADY PARSED THIS SOURCE with the host's own
            # `ast`, so a rejection here is not "invalid Python" -- it is
            # uasm accepting something narrower than full CPython (a
            # subset restriction enforced past the parser) while this
            # backend needs CPython's own compiler to accept the same text.
            # A crash would be a bug; the invariant that a refusal is
            # always a diagnostic holds here the same as everywhere else.
            raise BackendUnsupported(
                f"the pybc backend could not compile the original source "
                f"with the host interpreter: {exc}") from exc

        bit_field = 0  # timestamp-based header (PEP 552); bit 0 unset.
        mtime = 0
        size = len(source.text.encode("utf-8")) & 0xFFFFFFFF
        if source.path is not None:
            try:
                stat = source.path.stat()
            except OSError:
                pass
            else:
                mtime = int(stat.st_mtime) & 0xFFFFFFFF
                size = stat.st_size & 0xFFFFFFFF

        magic = _magic_number()
        header = struct.pack("<4sLLL", magic, bit_field, mtime, size)
        data = header + marshal.dumps(code)
        return {f"{stem}.pyc": data}


def _magic_number() -> bytes:
    """The running interpreter's own `.pyc` magic number.

    `importlib.util.MAGIC_NUMBER` is the ordinary way to ask; going through
    `sysconfig`/`_imp` is not needed here, this just names where the value
    actually comes from so a reader does not have to go look it up: it is
    two bytes of format version, then `\\r\\n` so a `.pyc` transferred in
    text mode is detectable as corrupted.
    """
    import importlib.util
    return importlib.util.MAGIC_NUMBER


register(PycBackend())
