"""SSA IR types consumed by ASMPython compiler backends.

ASMPython's own type strings collapse onto three machine-facing IR types at the
register-allocation level. Every backend receives shared build options through
its argument dictionaries, including speedy-lossy and sanitizer policy.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field


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
    operands: list


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
    # (setjmp_block_index, end_block_index) per try/except region. Exception
    # transfers are not ordinary CFG edges, so register allocation consumes this
    # metadata to keep values live across handlers without misclassifying the
    # synthetic backward edges as loops.
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
    # Names of `funcs` entries to publish as native-library exports (from
    # `@access(Public)` / `@abi(...)` -- see ast_nodes.FuncDef/ClassDef's
    # is_public_export and ir_lower.py's lower_module). Empty for an
    # ordinary standalone-executable compile. A library backend/linker
    # (pe_linker.py's PE export directory, elf_linker.py's ELF dynamic
    # symbol table) uses this instead of re-deriving publicness from the
    # AST, which it no longer has access to at link time.
    exports: list[str] = field(default_factory=list)


class IRBackend(abc.ABC):
    """Interface every ASMPython compiler backend implements.

    Every compile/link argument dictionary receives:

    ``speedy_lossy``
        Permit cheaper code generation that may produce slower/larger programs.
    ``bleach``
        Request the strong default sanitizer/checking policy.
    ``sanitizers``
        A normalized tuple of requested sanitizer names.

    These modes may affect performance and diagnostics, never language semantics.
    """

    @property
    @abc.abstractmethod
    def requested_args(self) -> list[dict]:
        """CLI arguments this backend wants the driver to register."""

    @property
    def default_linker(self) -> str:
        return "gcc"

    @property
    def production_suitable(self) -> bool:
        return True

    @abc.abstractmethod
    def compile(self, module: IRModule, args: dict) -> dict[str, bytes]:
        """Compile an IRModule to one or more named output files."""

    @abc.abstractmethod
    def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
        """Link object bytes into one or more named output files."""


class ModuleBackend(IRBackend):
    """Adapt a module-level backend implementation to :class:`IRBackend`."""

    def __init__(self, module: object) -> None:
        self._module = module

    @property
    def name(self) -> str:
        return str(getattr(self._module, "__name__", type(self._module).__name__))

    @property
    def requested_args(self) -> list[dict]:
        return getattr(self._module, "requested_args", [])

    @property
    def default_linker(self) -> str:
        return getattr(self._module, "default_linker", "gcc")

    @property
    def production_suitable(self) -> bool:
        return bool(getattr(self._module, "production_suitable", True))

    def compile(self, module: IRModule, args: dict) -> dict[str, bytes]:
        from .build_options import inject_build_options
        from .build_report import event, stage

        resolved = inject_build_options(args)
        with stage("backend.compile", backend=self.name):
            outputs = self._module.run_backend_codegen(  # type: ignore[attr-defined]
                module, resolved
            )
        event(
            "backend.outputs",
            backend=self.name,
            phase="compile",
            outputs={name: len(data) for name, data in outputs.items()},
        )
        return outputs

    def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
        from .build_options import inject_build_options
        from .build_report import event, stage

        resolved = inject_build_options(args)
        with stage("backend.link", backend=self.name, input_objects=len(objects)):
            outputs = self._module.run_backend_link(  # type: ignore[attr-defined]
                objects, resolved
            )
        event(
            "backend.outputs",
            backend=self.name,
            phase="link",
            outputs={name: len(data) for name, data in outputs.items()},
        )
        return outputs
