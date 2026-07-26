"""SSA IR types consumed by ASMPython compiler backends.

This IR is deliberately **language-neutral**: nothing here encodes Python (or
any source language's) semantics. Source-specific behavior -- boxing, dynamic
dispatch, builtin type ids, the exception object model -- lives entirely in a
frontend's *lowering* (e.g. ``ir_lower.py`` for the Python frontend) and the
runtime it calls, never in these types. Any frontend (C, Lua, a DSL) can target
this IR directly by emitting the same instruction/type vocabulary.

The type model is a small, machine-facing system:

* **Integers** ``i1/i8/i16/i32/i64`` (signed) and ``u8/u16/u32/u64``
  (unsigned). Signedness is carried on the type *and* on the op (``idiv`` vs
  ``udiv``, ``icmp.lt`` vs ``icmp.ult``); the type is the source of truth.
* **Floats** ``f32``/``f64``.
* **Pointers** ``ptr`` -- always 8 bytes. A pointer may optionally carry a
  ``pointee`` type for verification/layout; its ``name`` stays ``"ptr"`` so
  codegen treats every pointer identically.
* **Vectors** ``v128`` (SIMD).
* **Structs** -- aggregates with C-style layout (natural field alignment,
  trailing pad to the struct's own alignment). Struct *values* live in memory:
  a struct is manipulated through a ``ptr`` to its storage (``alloca`` of
  ``size_bytes``), fields via ``gep`` at ``field_offset(i)`` + a width-typed
  ``load``/``store``. No struct value ever occupies a register.

``name`` is the canonical machine tag every backend already switches on
(``i32``, ``u8``, ``f64``, ``ptr`` ...); ``kind``/``bits``/``signed``/
``size_bytes``/``align`` are derived from it, so existing ``IRType("i32")``
construction and ``.name`` reads keep working unchanged.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field


def _struct_layout(fields: "tuple[IRType, ...]") -> "tuple[tuple[int, ...], int, int]":
    """C-style layout for a struct: (field offsets, total size, alignment).

    Each field starts at the next offset that satisfies its own alignment;
    the struct's size is padded up to the largest field alignment so arrays
    of the struct stay aligned.
    """
    offsets: list[int] = []
    off = 0
    max_align = 1
    for f in fields:
        a = f.align
        if a > max_align:
            max_align = a
        off = (off + a - 1) & ~(a - 1)
        offsets.append(off)
        off += f.size_bytes
    total = (off + max_align - 1) & ~(max_align - 1) if fields else 0
    return tuple(offsets), total, max_align


@dataclass(frozen=True)
class IRType:
    #: Canonical machine tag backends switch on: i1/i8/.../i64, u8.../u64,
    #: f32/f64, ptr, v128, or a struct's (advisory) name.
    name: str
    #: Ordered field types for a struct aggregate; None for every scalar/ptr.
    fields: "tuple[IRType, ...] | None" = None
    #: Optional target type of a pointer (name stays "ptr"); None = opaque.
    pointee: "IRType | None" = None

    def __repr__(self) -> str:
        if self.fields is not None:
            return f"{self.name}{{{', '.join(f.name for f in self.fields)}}}"
        if self.pointee is not None:
            return f"ptr({self.pointee!r})"
        return self.name

    # ── classification (derived from `name`, the canonical tag) ──────────────
    @property
    def kind(self) -> str:
        """One of: int, float, ptr, vector, struct, other."""
        if self.fields is not None:
            return "struct"
        n = self.name
        if n == "ptr":
            return "ptr"
        if n == "v128":
            return "vector"
        c = n[:1]
        if c == "f":
            return "float"
        if c in ("i", "u"):
            return "int"
        return "other"

    @property
    def signed(self) -> bool:
        """True for signed integers (``i*``); False otherwise."""
        return self.kind == "int" and self.name[0] == "i"

    @property
    def bits(self) -> int:
        """Scalar bit width. Raises for struct/other."""
        k = self.kind
        if k in ("int", "float"):
            return int(self.name[1:])
        if k == "ptr":
            return 64
        if k == "vector":
            return 128
        raise ValueError(f"type {self.name!r} ({k}) has no scalar bit width")

    @property
    def size_bytes(self) -> int:
        """Storage size in bytes (i1 rounds up to 1)."""
        k = self.kind
        if k == "struct":
            return _struct_layout(self.fields)[1]
        if k == "ptr":
            return 8
        if k == "vector":
            return 16
        return max(1, (self.bits + 7) // 8)

    @property
    def align(self) -> int:
        """Natural alignment in bytes."""
        if self.kind == "struct":
            return _struct_layout(self.fields)[2]
        return self.size_bytes

    # ── struct helpers ───────────────────────────────────────────────────────
    @property
    def field_offsets(self) -> "tuple[int, ...]":
        if self.kind != "struct":
            raise ValueError(f"{self.name!r} is not a struct type")
        return _struct_layout(self.fields)[0]

    def field_offset(self, index: int) -> int:
        """Byte offset of field ``index`` -- use as the ``gep`` offset operand."""
        return self.field_offsets[index]

    def field_type(self, index: int) -> "IRType":
        if self.kind != "struct":
            raise ValueError(f"{self.name!r} is not a struct type")
        return self.fields[index]


class Visibility(enum.Enum):
    PUBLIC = "public"
    GLOBAL = "global"
    PRIVATE = "private"
    UNDEFINED = "undefined"


# Canonical scalar/pointer constants. i64/f64/ptr keep their historic names so
# existing frontends and the backends are untouched; the narrower widths make a
# non-Python frontend (C's int32/uint8/..., Lua's number) expressible directly.
I1 = IRType("i1")
I8 = IRType("i8")
I16 = IRType("i16")
I32 = IRType("i32")
I64 = IRType("i64")
U8 = IRType("u8")
U16 = IRType("u16")
U32 = IRType("u32")
U64 = IRType("u64")
F32 = IRType("f32")
F64 = IRType("f64")
PTR = IRType("ptr")
V128 = IRType("v128")


def int_type(bits: int, signed: bool = True) -> IRType:
    """Construct an integer type, e.g. ``int_type(32)`` -> i32, unsigned u32."""
    return IRType(f"{'i' if signed else 'u'}{bits}")


def float_type(bits: int) -> IRType:
    return IRType(f"f{bits}")


def ptr_to(pointee: IRType | None = None) -> IRType:
    """A pointer carrying an (optional) pointee type; ``name`` stays ``"ptr"``."""
    return IRType("ptr", pointee=pointee)


def struct_type(fields: "list[IRType] | tuple[IRType, ...]", name: str = "struct") -> IRType:
    """An aggregate with C-style layout. See :func:`_struct_layout`."""
    return IRType(name, fields=tuple(fields))


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


@dataclass(frozen=True)
class FrontendContext:
    """Everything a frontend needs to turn source text into a typed module.

    Mirrors the argument dictionaries backends receive: a frontend gets one
    of these instead of a loose kwargs bag so new pipeline knobs can be added
    without breaking every ``IRFrontend.parse`` signature. ``Path``-typed
    fields are left as forward-reference annotations (this module deliberately
    avoids importing ``pathlib`` at runtime).
    """

    source_dir: "object | None" = None      # Path | None
    entry_path: "object | None" = None       # Path | None
    whole_program: bool = True
    all_errors: bool = False
    active_extensions: "frozenset[str] | None" = None


class IRFrontend(abc.ABC):
    """Interface every ASMPython source-language frontend implements.

    A frontend is the mirror image of :class:`IRBackend`: where a backend turns
    the shared IR into artifacts, a frontend turns source text of some language
    into the typed AST *module* that the rest of the pipeline
    (``ir_lower`` -> a backend, or the legacy codegen) already consumes. This is
    the single "side chute" into the IR pipeline -- whatever a frontend returns
    from :meth:`parse` is handed straight to ``driver._run_backend`` exactly as
    the built-in Python path's sema'd module is.

    The default ``python`` frontend wraps the existing lexer/parser/sema. Any
    other language only has to produce a module object exposing the same surface
    the backends read (function/class defs, and flags like ``uses_overload`` /
    ``has_asm_source``).
    """

    #: Canonical selector name, e.g. ``"python"``. Set by implementations.
    name: str = ""
    #: Source-file extensions this frontend claims (advisory; ``()`` = any).
    source_extensions: tuple[str, ...] = ()
    #: Whether this frontend is fit for real builds (scaffolds set False).
    production_suitable: bool = True

    @property
    def requested_args(self) -> list[dict]:
        """CLI arguments this frontend wants the driver to register.

        Symmetric to :attr:`IRBackend.requested_args`; empty by default.
        """
        return []

    @abc.abstractmethod
    def parse(self, src: str, ctx: FrontendContext) -> object:
        """Parse+analyze ``src`` into the typed module the IR pipeline consumes."""


class IRPass(abc.ABC):
    """Interface every optimization/transform pass implements.

    A pass is a plain IR->IR transform over the *neutral* IR: it must not
    depend on which frontend produced the module (see ``ir_contract.md``). This
    is the third registration axis alongside :class:`IRFrontend` and
    :class:`IRBackend`, selected with ``--passes name1,name2``.

    ``requires``/``provides``/``preserves`` are a deliberately tiny invariant
    system (a set of string tags, e.g. ``"ssa"``), just enough for the pass
    manager to reject an impossible ordering -- not LLVM's full analysis
    -preservation machinery. Keep it small: legibility is the point.
    """

    #: Canonical selector name, e.g. ``"mem2reg"``. Set by implementations.
    name: str = ""
    #: One-line description, shown by ``--passes help``.
    description: str = ""
    #: Invariants that must hold before this pass runs (e.g. ``{"ssa"}``).
    requires: frozenset[str] = frozenset()
    #: Invariants this pass establishes (mem2reg provides ``"ssa"``).
    provides: frozenset[str] = frozenset()
    #: Invariants this pass keeps intact; anything else it may invalidate.
    preserves: frozenset[str] = frozenset({"cfg"})

    @abc.abstractmethod
    def run(self, module: IRModule) -> bool:
        """Transform ``module`` in place. Return True if anything changed."""
