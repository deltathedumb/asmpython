"""SSA IR types consumed by asmpython's built-in x86-64 backend
(asmpython/_backends/x86_64), the codegen.py-free path reached via
driver.py's --backend x86-64. The backend's `run_backend_codegen(ir,
args)` only ever touches `.funcs`/`.data` and every nested field by name
(duck-typed, no import cycle back to this module from the backend).

asmpython's own type system (see ast_nodes.expr_type: "int", "float",
"str", "list", "dict", "instance:Name", ...) collapses onto two IRTypes
at the register-allocation level, mirroring what the legacy NASM-text
codegen.py already does by hand: float values live in XMM registers
(F64); everything else -- ints, bools, and every heap pointer (str/list/
dict/instance/closure) -- is a 64-bit GP value (I64). There is no
"struct" IRType because asmpython has none at the machine level: every
object is a runtime dict, accessed through `_runtime_dict_*` calls, not
through typed field offsets.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
import enum


@dataclass(frozen=True)
class IRType:
    name: str

    def __repr__(self) -> str:
        return self.name


class Visibility(enum.Enum):
    PUBLIC = "public"
    GLOBAL = "global"
    PRIVATE = "private"
    UNDEFINED = "undefined"


I64 = IRType("i64")
F64 = IRType("f64")
PTR = IRType("ptr")

# asmpython's own type strings -> the IRType a value of that type is
# stored in. Anything not listed here (instance:*, list, dict, str,
# closures, ...) is a heap pointer and defaults to PTR.
_ASM_TYPE_TO_IR = {
    "int": I64,
    "bool": I64,
    "float": F64,
}


def ir_type_for(asm_type: str) -> IRType:
    return _ASM_TYPE_TO_IR.get(asm_type, PTR)


@dataclass
class IRValue:
    name: str
    type: IRType


@dataclass
class IRInstr:
    op: str
    result: IRValue | None
    operands: list  # IRValue | int | float | str


@dataclass
class IRBlock:
    label: str
    instrs: list[IRInstr] = field(default_factory=list)


@dataclass
class IRFunc:
    name: str
    params: list[IRValue]
    ret_type: IRType | None
    blocks: list[IRBlock] = field(default_factory=list)
    visibility: Visibility = Visibility.UNDEFINED
    # (setjmp_block_index, end_block_index) per try/except statement this
    # function lowers -- see ir_lower.py's _lower_try. The exception
    # handler blocks a setjmp call can transfer to via longjmp are NOT
    # connected to it by any ordinary br/br.t edge (the transfer only
    # happens through the runtime jmp_buf mechanism), and _lower_try
    # allocates them AFTER the try's own post-loop-body continuation
    # block in block-list order -- so a value defined before the try and
    # read inside the handler can look, to a plain block-list-order
    # liveness scan, already dead by the time the handler's use is
    # recorded. The x86-64 backend's regalloc.py consumes this to treat
    # each region as one liveness span, the same way it already does for
    # loop back-edges.
    #
    # Also consumed by regalloc.py's _last_uses to exclude every backward-
    # by-block-index branch _lower_try's control-flow-dispatch machinery
    # produces (a normal-completion `br` back to the try's own `end_b`, a
    # per-handler type-match `br.t` whose matched-target sits at a lower
    # index) from loop-back-edge detection -- none of them are real loops.
    try_regions: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class IRGlobal:
    name: str
    type: IRType
    value: int | float | str | list | None = None
    tls: bool = False


@dataclass
class IRModule:
    funcs: list[IRFunc] = field(default_factory=list)
    data: list[IRGlobal] = field(default_factory=list)


class IRBackend(abc.ABC):
    """Interface every asmpython compiler backend implements.

    In-process Python backends (the built-in x86-64 backend in
    asmpython/_backends/x86_64, anything else written directly against
    this IR) implement it directly. A future DLL-based custom backend
    wouldn't implement it itself -- it'd be wrapped by an adapter that
    loads the DLL and marshals `compile()` calls across that boundary via
    a serialized form of this IR (a DLL can't receive live IRModule/
    IRValue objects directly), so the driver only ever talks to an
    IRBackend either way and never needs to know which kind it has.
    """

    @property
    @abc.abstractmethod
    def requested_args(self) -> list[dict]:
        """CLI arguments this backend wants the driver to register and
        pass through (e.g. --target-os, --abi)."""

    @property
    def default_linker(self) -> str:
        """Name of the linker (asmpython/_linkers/<name>.py) this backend
        links with when driver.py's --linker flag isn't given explicitly.
        Backends "define" their linker this way -- e.g. the x86-64
        backend defaults to "builtin" (its whole point is skipping
        external tools); a backend with no opinion just inherits this
        default of "gcc"."""
        return "gcc"

    @abc.abstractmethod
    def compile(self, module: IRModule, args: dict) -> dict[str, bytes]:
        """Compile an IRModule to one or more output files, returned as
        {filename: bytes} (e.g. {"output.obj": b"..."})."""

    @abc.abstractmethod
    def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
        """Link one or more object files (as bytes) into one or more output files, returned as
        {filename: bytes} (e.g. {"output.exe": b"..."})."""


class ModuleBackend(IRBackend):
    """Adapts a plugin module exposing module-level `requested_args` and
    `run_backend_codegen(ir, args)` (see asmpython/_backends/x86_64's
    __init__.py for the reference implementation of this convention) to
    the IRBackend interface, so that backend is usable here unmodified."""

    def __init__(self, module: object) -> None:
        self._module = module

    @property
    def requested_args(self) -> list[dict]:
        return getattr(self._module, "requested_args", [])

    @property
    def default_linker(self) -> str:
        return getattr(self._module, "default_linker", "gcc")

    def compile(self, module: IRModule, args: dict) -> dict[str, bytes]:
        return self._module.run_backend_codegen(module, args)  # type: ignore[attr-defined]

    def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
        return self._module.run_backend_link(objects, args)  # type: ignore[attr-defined]
