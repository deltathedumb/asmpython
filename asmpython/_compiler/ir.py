"""SSA IR types, duck-type-compatible with uasm's x86-64 backend contract
(see C:\\Users\\M\\Documents\\Coding\\uasm\\compiler\\uasm.py and
compiler\\backends\\x86_64\\*.py). No import dependency on uasm itself --
the backend's `run_backend_codegen(ir, args)` only ever touches `.funcs`
and `.data`, and every nested field by name, so a same-shaped local
dataclass is all that's required to hand a module to it directly.

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

    In-process Python backends (uasm's x86_64 backend, anything else
    written directly against this IR) implement it directly. A future
    DLL-based custom backend wouldn't implement it itself -- it'd be
    wrapped by an adapter that loads the DLL and marshals `compile()`
    calls across that boundary via a serialized form of this IR (a DLL
    can't receive live IRModule/IRValue objects directly), so the driver
    only ever talks to an IRBackend either way and never needs to know
    which kind it has.
    """

    @property
    @abc.abstractmethod
    def requested_args(self) -> list[dict]:
        """CLI arguments this backend wants the driver to register and
        pass through (e.g. --target-os, --abi)."""

    @abc.abstractmethod
    def compile(self, module: IRModule, args: dict) -> dict[str, bytes]:
        """Compile an IRModule to one or more output files, returned as
        {filename: bytes} (e.g. {"output.obj": b"..."})."""


class ModuleBackend(IRBackend):
    """Adapts a plugin module exposing module-level `requested_args` and
    `run_backend_codegen(ir, args)` -- uasm's existing convention (see
    compiler/backends/x86_64/__init__.py) -- to the IRBackend interface,
    so that backend is usable here unmodified."""

    def __init__(self, module: object) -> None:
        self._module = module

    @property
    def requested_args(self) -> list[dict]:
        return getattr(self._module, "requested_args", [])

    def compile(self, module: IRModule, args: dict) -> dict[str, bytes]:
        return self._module.run_backend_codegen(module, args)  # type: ignore[attr-defined]
