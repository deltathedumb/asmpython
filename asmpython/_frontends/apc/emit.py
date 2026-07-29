"""APC -> neutral SSA IR.

Locals are emitted as **plain SSA values wherever that is trivially safe** --
assigned exactly once and never address-taken, so the definition dominates every
use in its lexical scope. Only locals that are reassigned (or whose address is
taken) get an ``alloca`` slot.

That split matters: ``mem2reg`` is the pass that would otherwise recover those
values, and it is deliberately out of the ``o1``/``o2`` presets pending a
register-allocator liveness fix (see ``_passes/mem2reg.py``). Emitting the easy
cases directly means most APC code does not wait on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..._compiler.ir import (
    F32, F64, I64, PTR, IRBlock, IRFunc, IRGlobal, IRInstr, IRModule, IRType,
    IRValue, Visibility,
)
from . import ast_nodes as A
from . import types as T
from .errors import APCError

_INT_BINOPS = {
    "+": "iadd", "-": "isub", "*": "imul",
    "&": "iand", "|": "ior", "^": "ixor", "<<": "shl",
}
_FLOAT_BINOPS = {"+": "fadd", "-": "fsub", "*": "fmul", "/": "fdiv"}
_CMP_SUFFIX = {"is": "eq", "==": "eq", "!=": "ne",
               "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}


@dataclass
class Val:
    """An emitted value, plus the layout it points at (when it points at one)."""
    ir: IRValue
    layout: str | None = None

    @property
    def type(self) -> IRType:
        return self.ir.type


@dataclass
class Slot:
    ptr: IRValue
    type: IRType
    layout: str | None = None
    mutable: bool = True


class _Layout:
    __slots__ = ("name", "offsets", "sizes", "types", "size")

    def __init__(self, name: str) -> None:
        self.name = name
        self.offsets: dict[str, int] = {}
        self.sizes: dict[str, int] = {}
        # A `layout` field has geometry only, so it reads back as the unsigned
        # type of its width and `as` reinterprets it. A `type` field was
        # declared with a real type, so that type is kept and used verbatim.
        self.types: dict[str, IRType] = {}
        self.size = 0


class _Type:
    """A ``type`` declaration: a layout plus its methods."""

    __slots__ = ("name", "parent", "layout", "methods", "plain", "ctor", "ns")

    def __init__(self, name: str, ns: str | None = None) -> None:
        self.name = name
        self.ns = ns
        self.parent: str | None = None
        self.layout = _Layout(name)
        self.methods: dict[str, A.FuncDecl] = {}
        self.plain: set[str] = set()
        self.ctor: A.FuncDecl | None = None

    def symbol(self, method: str) -> str:
        return f"{self.name}__{method}"


class ModuleEmitter:
    def __init__(self, src: str) -> None:
        self.src = src
        self.module = IRModule()
        self.layouts: dict[str, _Layout] = {}
        self.types: dict[str, _Type] = {}
        self.enums: dict[str, dict[str, int]] = {}
        self.consts: dict[str, tuple[object, IRType]] = {}
        self.signatures: dict[str, A.FuncDecl] = {}
        self.symbols: dict[str, str] = {}
        self.module_members: dict[str, set[str]] = {}
        self.exports: list[str] = []
        self._strings: dict[str, str] = {}

    # ── diagnostics ──────────────────────────────────────────────────────
    def err(self, msg: str, node: A.Node) -> APCError:
        return APCError(msg, getattr(node, "line", 0), getattr(node, "col", 0), self.src)

    # ── entry ────────────────────────────────────────────────────────────
    def run(self, mod: A.Module) -> IRModule:
        funcs: list[tuple[A.FuncDecl, str, str | None]] = []
        self._collect(mod.decls, funcs, ns=None)

        for decl, symbol, ns in funcs:
            if decl.is_extern:
                continue
            self.module.funcs.append(
                FuncEmitter(self, decl, symbol=symbol, ns=ns).run())

        # A namespaced type is registered under both its plain and qualified
        # name (`FrameBuffer` and `framebuf::FrameBuffer`) and both keys map to
        # one object -- emit its methods once.
        seen: set[int] = set()
        for ty in self.types.values():
            if id(ty) in seen:
                continue
            seen.add(id(ty))
            for mname, mdecl in ty.methods.items():
                if mdecl.is_extern or mdecl.body is None:
                    continue
                self.module.funcs.append(
                    FuncEmitter(self, mdecl, owner=ty, ns=ty.ns).run())

        defined = {f.name for f in self.module.funcs}
        for name in self.exports:
            if name in defined:
                self.module.exports.append(name)
                for f in self.module.funcs:
                    if f.name == name:
                        f.visibility = Visibility.PUBLIC
        return self.module

    # ── module-level collection ──────────────────────────────────────────
    def _collect(self, decls: list, funcs: list, ns: str | None) -> None:
        """Register one module's declarations.

        ``ns`` is the short module name for imported code (``framebuf`` for
        ``import std::framebuf``). Imported names are reachable as
        ``framebuf::new``, and emit as the flat symbol ``framebuf__new`` so two
        modules can each define ``new`` without colliding.
        """
        def qualify(name: str) -> str:
            return name if ns is None else f"{ns}::{name}"

        def symbolize(name: str) -> str:
            return name if ns is None else f"{ns}__{name}"

        for decl in decls:
            if isinstance(decl, A.ImportDecl):
                self._load_import(decl, funcs)
            elif isinstance(decl, A.LayoutDecl):
                self._collect_layout(decl)
                if ns is not None:
                    self.layouts[qualify(decl.name)] = self.layouts[decl.name]
            elif isinstance(decl, A.TypeDecl):
                self._collect_type(decl, ns)
                if ns is not None:
                    self.types[qualify(decl.name)] = self.types[decl.name]
                    self.layouts[qualify(decl.name)] = self.layouts[decl.name]
            elif isinstance(decl, A.EnumDecl):
                self._collect_enum(decl)
                if ns is not None:
                    self.enums[qualify(decl.name)] = self.enums[decl.name]
            elif isinstance(decl, A.ExportDecl):
                self.exports.extend(n.split("::")[-1] for n in decl.names)
            elif isinstance(decl, A.VarDecl):
                self._collect_const(decl)
            elif isinstance(decl, A.FuncDecl):
                call_name = qualify(decl.name)
                symbol = decl.name if decl.is_extern else symbolize(decl.name)
                self.signatures[call_name] = decl
                self.symbols[call_name] = symbol
                if ns is not None:
                    self.module_members.setdefault(ns, set()).add(decl.name)
                funcs.append((decl, symbol, ns))

    def _load_import(self, decl: A.ImportDecl, funcs: list) -> None:
        from pathlib import Path

        from .parser import parse as _parse

        parts = decl.module.split("::")
        if parts[0] != "std":
            raise self.err(
                f"unknown module {decl.module!r} -- only 'std::*' resolves today",
                decl)
        short = parts[-1]
        if short in self.module_members:
            return                                  # already imported
        path = Path(__file__).resolve().parent.joinpath(*parts).with_suffix(".apc")
        if not path.is_file():
            raise self.err(f"no module {decl.module!r} at {path}", decl)

        self.module_members.setdefault(short, set())
        self._declare_native_libraries(path, decl)
        text = path.read_text(encoding="utf-8")
        outer_src, self.src = self.src, text
        try:
            self._collect(_parse(text).decls, funcs, ns=short)
        finally:
            self.src = outer_src

    def _declare_native_libraries(self, module_path, decl: A.ImportDecl) -> None:
        """Register the OS libraries a std module calls into.

        A module's dependencies are its own business, and the backends'
        builtin symbol tables are deliberately not the place for them -- a
        linker must not know that ``std::framebuf`` exists. Each module ships
        a sibling ``.libs`` file in ``native_libraries`` declaration syntax
        (``user32.dll:CreateWindowExA,ShowWindow``), and those go into the
        registry the driver already consults for ``--link-library``.
        """
        libs = module_path.with_suffix(".libs")
        if not libs.is_file():
            return

        from ..._compiler import native_libraries as native

        registry = native.active_registry()
        for lineno, raw in enumerate(libs.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            target_os = None
            if "@" in line:
                line, _, target_os = (p.strip() for p in line.partition("@"))
            try:
                registry.declare(native.parse_declaration(line, target_os=target_os or None))
            except native.NativeLibraryError as exc:
                raise self.err(f"{libs.name}:{lineno}: {exc}", decl) from exc

    def _collect_layout(self, decl: A.LayoutDecl) -> None:
        lay = _Layout(decl.name)
        cursor = 0
        for f in decl.fields:
            off = cursor if f.offset is None else f.offset
            lay.offsets[f.name] = off
            lay.sizes[f.name] = f.size
            ty = T.unsigned_of_size(f.size)
            if ty is not None:
                lay.types[f.name] = ty
            cursor = off + f.size
            lay.size = max(lay.size, cursor)
        self.layouts[decl.name] = lay

    def _collect_type(self, decl: A.TypeDecl, ns: str | None = None) -> None:
        ty = _Type(decl.name, ns)
        ty.parent = decl.parent
        for m in decl.methods:
            if m.name == "constructor":
                ty.ctor = m
            else:
                ty.methods[m.name] = m
                if m.is_plain:
                    ty.plain.add(m.name)
        if ty.ctor is not None:
            ty.methods["constructor"] = ty.ctor
            self._derive_fields(ty, ty.ctor)
        self.types[decl.name] = ty
        self.layouts[decl.name] = ty.layout

    def _derive_fields(self, ty: _Type, ctor: A.FuncDecl) -> None:
        """A type's layout is the ordered ``pub const Parent.X`` set in its
        constructor. They must sit at the constructor's top level: a field
        declared inside an ``if`` would imply a size that depends on a runtime
        value, which cannot be compiled."""
        offset = 0
        align_max = 1
        for stmt in ctor.body or []:
            if not isinstance(stmt, A.FieldDecl):
                continue
            irty = T.scalar(stmt.type_name or "") or I64
            if stmt.type_name in self.types or stmt.type_name in self.layouts:
                irty = PTR
            align = irty.align
            align_max = max(align_max, align)
            offset = (offset + align - 1) & ~(align - 1)
            ty.layout.offsets[stmt.name] = offset
            ty.layout.sizes[stmt.name] = irty.size_bytes
            ty.layout.types[stmt.name] = irty
            offset += irty.size_bytes
        ty.layout.size = (offset + align_max - 1) & ~(align_max - 1)
        self._reject_nested_fields(ctor)

    def _reject_nested_fields(self, ctor: A.FuncDecl) -> None:
        def walk(node, top: bool) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item, top)
                return
            if not isinstance(node, A.Node):
                return
            if isinstance(node, A.FieldDecl) and not top:
                raise self.err(
                    f"field {node.name!r} is declared inside control flow -- a "
                    "type's layout must not depend on a runtime value", node)
            for attr in ("body", "then_body", "else_body"):
                walk(getattr(node, attr, None), False)

        for stmt in ctor.body or []:
            for attr in ("body", "then_body", "else_body"):
                walk(getattr(stmt, attr, None), False)

    def _collect_enum(self, decl: A.EnumDecl) -> None:
        members: dict[str, int] = {}
        auto = 0
        for m in decl.members:
            if m.value is None:
                members[m.name] = auto
                auto += 1
            else:
                if not isinstance(m.value, A.Literal) or m.value.kind != "int":
                    raise self.err("enum member value must be an integer literal", m)
                members[m.name] = int(m.value.value)
                auto = members[m.name] + 1
        self.enums[decl.name] = members

    _FOLD_BINOPS = {
        "+": lambda a, b: a + b, "-": lambda a, b: a - b,
        "*": lambda a, b: a * b, "|": lambda a, b: a | b,
        "&": lambda a, b: a & b, "^": lambda a, b: a ^ b,
        "<<": lambda a, b: a << b, ">>": lambda a, b: a >> b,
    }

    def _fold(self, node):
        """Evaluate a module-level constant initializer, or return None.

        Only literals, other module constants, and arithmetic over them --
        enough for `0 - 16` or `0xFF000000 | 0xFF`, which is what constants
        are actually written as, without becoming an interpreter.
        """
        if isinstance(node, A.Literal) and node.kind in ("int", "float"):
            return node.value
        if isinstance(node, A.Literal) and node.kind == "bool":
            return 1 if node.value else 0
        if isinstance(node, A.Name):
            known = self.consts.get(node.ident)
            return None if known is None else known[0]
        if isinstance(node, A.Unary):
            inner = self._fold(node.operand)
            if inner is None:
                return None
            if node.op == "-":
                return -inner
            if node.op == "~":
                return ~int(inner)
            return None
        if isinstance(node, A.Binary):
            op = self._FOLD_BINOPS.get(node.op)
            if op is None:
                return None
            lhs, rhs = self._fold(node.lhs), self._fold(node.rhs)
            if lhs is None or rhs is None:
                return None
            if node.op == "/" and not rhs:
                return None
            return op(lhs, rhs)
        return None

    def _collect_const(self, decl: A.VarDecl) -> None:
        if decl.value is None:
            raise self.err(f"module-level '{decl.name}' needs an initializer", decl)
        if isinstance(decl.value, A.Literal) and decl.value.kind == "str":
            self.consts[decl.name] = (decl.value.value, PTR)
            return
        folded = self._fold(decl.value)
        if folded is None:
            raise self.err(
                f"module-level '{decl.name}' must fold to a constant "
                "(literals and arithmetic over other constants)", decl)
        ty = T.scalar(decl.type_name or "") or (
            F64 if isinstance(folded, float) else I64)
        self.consts[decl.name] = (folded, ty)

    def intern_string(self, text: str) -> str:
        name = self._strings.get(text)
        if name is None:
            # Leading-dot names collide with COFF section naming and end up as
            # undefined symbols at link time; mirror ir_lower's `__str_N`.
            name = f"__apc_str_{len(self._strings)}"
            self._strings[text] = name
            self.module.data.append(IRGlobal(name, PTR, text))
        return name


class FuncEmitter:
    def __init__(self, mod: ModuleEmitter, decl: A.FuncDecl,
                 owner: "_Type | None" = None, symbol: str | None = None,
                 ns: str | None = None) -> None:
        self.mod = mod
        self.decl = decl
        self.owner = owner
        self.symbol = symbol
        self.ns = ns
        self.blocks: list[IRBlock] = []
        self.cur: IRBlock | None = None
        self.scopes: list[dict[str, Slot | Val]] = []
        self.loops: list[tuple[str, str]] = []
        self._tmp = 0
        self._label = 0
        self.ret_type: IRType | None = (
            None if T.is_void(decl.ret_type) else self._ir_type(decl.ret_type, decl))

    # ── helpers ──────────────────────────────────────────────────────────
    def err(self, msg: str, node: A.Node) -> APCError:
        return self.mod.err(msg, node)

    def _ir_type(self, name: str | None, node: A.Node) -> IRType:
        if name is None:
            return I64
        ty = T.scalar(name)
        if ty is not None:
            return ty
        if name in self.mod.layouts or name in self.mod.types:
            return PTR
        if name in self.mod.enums:
            return I64
        if name.startswith("bytes["):
            return PTR
        raise self.err(f"unknown type {name!r}", node)

    def tmp(self, ty: IRType) -> IRValue:
        self._tmp += 1
        return IRValue(f"t{self._tmp}", ty)

    def label(self, stem: str) -> str:
        self._label += 1
        return f"{stem}.{self._label}"

    def new_block(self, label: str) -> IRBlock:
        blk = IRBlock(label)
        self.blocks.append(blk)
        self.cur = blk
        return blk

    def terminated(self) -> bool:
        return bool(self.cur and self.cur.instrs
                    and self.cur.instrs[-1].op in ("br", "br.t", "ret"))

    def emit(self, op: str, result: IRValue | None, operands: list) -> IRValue | None:
        if self.terminated():
            self.new_block(self.label("unreachable"))
        assert self.cur is not None
        self.cur.instrs.append(IRInstr(op, result, operands))
        return result

    def const(self, value, ty: IRType) -> Val:
        dst = self.tmp(ty)
        self.emit("const", dst, [value])
        return Val(dst)

    def branch(self, label: str) -> None:
        if not self.terminated():
            self.emit("br", None, [label])

    # ── scopes ───────────────────────────────────────────────────────────
    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        self.scopes.pop()

    def declare(self, name: str, binding) -> None:
        self.scopes[-1][name] = binding

    def lookup(self, name: str):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    # ── slot analysis ────────────────────────────────────────────────────
    def needs_slot(self, body: list) -> set[str]:
        """Names that must live in memory: reassigned, or address-taken."""
        assigns: dict[str, int] = {}
        addressed: set[str] = set()

        def walk(node) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if not isinstance(node, A.Node):
                return
            if isinstance(node, A.VarDecl) and node.value is not None:
                assigns[node.name] = assigns.get(node.name, 0) + 1
            elif isinstance(node, A.Assign) and isinstance(node.target, A.Name):
                assigns[node.target.ident] = assigns.get(node.target.ident, 0) + 1
            elif isinstance(node, A.For):
                assigns[node.var] = assigns.get(node.var, 0) + 2  # loop-carried
            elif isinstance(node, A.Unary) and node.op == "&":
                if isinstance(node.operand, A.Name):
                    addressed.add(node.operand.ident)
            for attr in ("value", "expr", "cond", "operand", "lhs", "rhs",
                         "obj", "index", "callee", "target", "start", "end"):
                walk(getattr(node, attr, None))
            for attr in ("body", "then_body", "else_body", "args"):
                walk(getattr(node, attr, None))

        walk(body)
        return {n for n, c in assigns.items() if c > 1} | addressed

    # ── entry ────────────────────────────────────────────────────────────
    def run(self) -> IRFunc:
        params: list[IRValue] = []
        name = self.symbol or self.decl.name
        self_val: IRValue | None = None

        if self.owner is not None:
            name = self.owner.symbol(self.decl.name)
            if self.decl.name not in self.owner.plain:
                # An instance method takes its receiver as parameter 0, which
                # is what `Parent` names inside the body.
                self_val = IRValue("Parent", PTR)
                params.append(self_val)

        for p in self.decl.params:
            params.append(IRValue(p.name, self._ir_type(p.type_name, p)))

        func = IRFunc(name, params, self.ret_type)
        self.new_block("entry")
        self.push_scope()
        if self_val is not None:
            self.declare("Parent", Val(self_val, self.owner.name))

        body = self.decl.body or []
        slotted = self.needs_slot(body)

        # `params` may carry the receiver at index 0, which `decl.params` does
        # not -- zip from past it or every declared name binds one slot early.
        declared = params[1:] if self_val is not None else params
        for p, irv in zip(self.decl.params, declared):
            layout = p.type_name if p.type_name in self.mod.layouts else None
            if p.name in slotted:
                slot = self._alloca(irv.type, layout)
                self.emit("store", None, [irv, slot.ptr])
                self.declare(p.name, slot)
            else:
                self.declare(p.name, Val(irv, layout))

        self.slotted = slotted
        self.stmts(body)

        if not self.terminated():
            if self.ret_type is None:
                self.emit("ret", None, [])
            else:
                zero = self.const(0, self.ret_type)
                self.emit("ret", None, [zero.ir])

        self.pop_scope()
        func.blocks = self.blocks
        return func

    def _alloca(self, ty: IRType, layout: str | None = None) -> Slot:
        ptr = self.tmp(PTR)
        nbytes = self.mod.layouts[layout].size if layout else ty.size_bytes
        entry = self.blocks[0]
        entry.instrs.insert(0, IRInstr("alloca", ptr, [max(8, nbytes)]))
        return Slot(ptr, ty, layout)

    # ── statements ───────────────────────────────────────────────────────
    def stmts(self, body: list) -> None:
        for stmt in body:
            self.stmt(stmt)

    def stmt(self, node) -> None:
        if isinstance(node, A.VarDecl):
            return self.stmt_var(node)
        if isinstance(node, A.Assign):
            return self.stmt_assign(node)
        if isinstance(node, A.If):
            return self.stmt_if(node)
        if isinstance(node, A.While):
            return self.stmt_while(node)
        if isinstance(node, A.For):
            return self.stmt_for(node)
        if isinstance(node, A.Return):
            return self.stmt_return(node)
        if isinstance(node, A.ExprStmt):
            self.expr(node.expr)
            return None
        if isinstance(node, A.FieldDecl):
            return self.stmt_field(node)
        if isinstance(node, A.Break):
            if not self.loops:
                raise self.err("'break' outside a loop", node)
            self.branch(self.loops[-1][1])
            return None
        if isinstance(node, A.Continue):
            if not self.loops:
                raise self.err("'continue' outside a loop", node)
            self.branch(self.loops[-1][0])
            return None
        raise self.err(f"unsupported statement {type(node).__name__}", node)

    def stmt_field(self, node: A.FieldDecl) -> None:
        """``pub const Parent.Red: int = red`` -- store into the receiver."""
        if self.owner is None:
            raise self.err("a field can only be declared inside a type", node)
        binding = self.lookup("Parent")
        if not isinstance(binding, Val):
            raise self.err("'Parent' is not available here", node)
        lay = self.owner.layout
        if node.name not in lay.offsets:
            raise self.err(f"unknown field {node.name!r}", node)
        ty = lay.types[node.name]
        val = self.coerce(self.expr(node.value, want=ty), ty, node)
        addr = self.tmp(PTR)
        self.emit("gep", addr, [binding.ir, lay.offsets[node.name]])
        self.emit("store", None, [val.ir, addr])

    def stmt_var(self, node: A.VarDecl) -> None:
        declared = self._ir_type(node.type_name, node) if node.type_name else None
        layout = node.type_name if node.type_name in self.mod.layouts else None

        if node.value is None:
            slot = self._alloca(declared or I64, layout)
            slot.mutable = node.mutable
            self.declare(node.name, slot)
            return

        val = self.expr(node.value, want=declared)
        if declared is not None and val.type.name != declared.name:
            val = self.coerce(val, declared, node)
        if layout is None:
            layout = val.layout

        if node.name in getattr(self, "slotted", ()):
            slot = self._alloca(val.type, layout)
            slot.mutable = node.mutable
            self.emit("store", None, [val.ir, slot.ptr])
            self.declare(node.name, slot)
        else:
            self.declare(node.name, Val(val.ir, layout))

    def stmt_assign(self, node: A.Assign) -> None:
        if isinstance(node.target, A.Name):
            binding = self.lookup(node.target.ident)
            if binding is None:
                raise self.err(f"undefined variable {node.target.ident!r}", node)
            if isinstance(binding, Val) or not binding.mutable:
                raise self.err(
                    f"{node.target.ident!r} is immutable -- declared with "
                    "'const'; use 'let' to reassign it", node)
            val = self.expr(node.value, want=binding.type)
            val = self.coerce(val, binding.type, node)
            self.emit("store", None, [val.ir, binding.ptr])
            return
        if isinstance(node.target, A.Member):
            ptr, ty = self.member_address(node.target)
            val = self.expr(node.value, want=ty)
            val = self.coerce(val, ty, node)
            self.emit("store", None, [val.ir, ptr])
            return
        if isinstance(node.target, A.Index):
            ptr, ty = self.index_address(node.target)
            val = self.expr(node.value, want=ty)
            val = self.coerce(val, ty, node)
            self.emit("store", None, [val.ir, ptr])
            return
        raise self.err("invalid assignment target", node)

    def stmt_if(self, node: A.If) -> None:
        cond = self.truth(self.expr(node.cond))
        then_l = self.label("if.then")
        else_l = self.label("if.else") if node.else_body else None
        end_l = self.label("if.end")

        self.emit("br.t", None, [cond.ir, then_l, else_l or end_l])

        self.new_block(then_l)
        self.push_scope()
        self.stmts(node.then_body)
        self.pop_scope()
        self.branch(end_l)

        if else_l is not None:
            self.new_block(else_l)
            self.push_scope()
            self.stmts(node.else_body)
            self.pop_scope()
            self.branch(end_l)

        self.new_block(end_l)

    def stmt_while(self, node: A.While) -> None:
        head_l = self.label("while.head")
        body_l = self.label("while.body")
        end_l = self.label("while.end")

        self.branch(head_l)
        self.new_block(head_l)
        cond = self.truth(self.expr(node.cond))
        self.emit("br.t", None, [cond.ir, body_l, end_l])

        self.new_block(body_l)
        self.loops.append((head_l, end_l))
        self.push_scope()
        self.stmts(node.body)
        self.pop_scope()
        self.loops.pop()
        self.branch(head_l)

        self.new_block(end_l)

    def stmt_for(self, node: A.For) -> None:
        start = self.expr(node.start)
        limit = self.expr(node.end)
        slot = self._alloca(I64)
        self.emit("store", None, [self.coerce(start, I64, node).ir, slot.ptr])

        head_l = self.label("for.head")
        body_l = self.label("for.body")
        step_l = self.label("for.step")
        end_l = self.label("for.end")

        limit_slot = self._alloca(I64)
        self.emit("store", None, [self.coerce(limit, I64, node).ir, limit_slot.ptr])

        self.branch(head_l)
        self.new_block(head_l)
        cur = self.tmp(I64)
        self.emit("load", cur, [slot.ptr])
        lim = self.tmp(I64)
        self.emit("load", lim, [limit_slot.ptr])
        test = self.tmp(I64)
        self.emit("icmp.lt", test, [cur, lim])
        self.emit("br.t", None, [test, body_l, end_l])

        self.new_block(body_l)
        self.push_scope()
        self.declare(node.var, slot)
        self.loops.append((step_l, end_l))
        self.stmts(node.body)
        self.loops.pop()
        self.pop_scope()
        self.branch(step_l)

        self.new_block(step_l)
        iv = self.tmp(I64)
        self.emit("load", iv, [slot.ptr])
        one = self.const(1, I64)
        nxt = self.tmp(I64)
        self.emit("iadd", nxt, [iv, one.ir])
        self.emit("store", None, [nxt, slot.ptr])
        self.branch(head_l)

        self.new_block(end_l)

    def stmt_return(self, node: A.Return) -> None:
        # `ret none` in a `none` function is a bare return, not a value.
        if node.value is None or (
            self.ret_type is None
            and isinstance(node.value, A.Literal)
            and node.value.kind == "none"
        ):
            self.emit("ret", None, [])
            return
        val = self.expr(node.value, want=self.ret_type)
        if self.ret_type is None:
            raise self.err("returning a value from a 'none' function", node)
        val = self.coerce(val, self.ret_type, node)
        self.emit("ret", None, [val.ir])

    # ── expressions ──────────────────────────────────────────────────────
    def expr(self, node, want: IRType | None = None) -> Val:
        if isinstance(node, A.Literal):
            return self.expr_literal(node, want)
        if isinstance(node, A.Name):
            return self.expr_name(node)
        if isinstance(node, A.NsAccess):
            return self.expr_ns(node)
        if isinstance(node, A.Binary):
            return self.expr_binary(node)
        if isinstance(node, A.Unary):
            return self.expr_unary(node)
        if isinstance(node, A.Call):
            return self.expr_call(node)
        if isinstance(node, A.Cast):
            return self.expr_cast(node)
        if isinstance(node, A.Member):
            ptr, ty = self.member_address(node)
            dst = self.tmp(ty)
            self.emit("load", dst, [ptr])
            return Val(dst)
        if isinstance(node, A.Index):
            ptr, ty = self.index_address(node)
            dst = self.tmp(ty)
            self.emit("load", dst, [ptr])
            return Val(dst)
        if isinstance(node, A.SizeOf):
            return self.const(self.sizeof(node.type_name, node), I64)
        raise self.err(f"unsupported expression {type(node).__name__}", node)

    def expr_literal(self, node: A.Literal, want: IRType | None) -> Val:
        if node.kind == "str":
            name = self.mod.intern_string(str(node.value))
            dst = self.tmp(PTR)
            self.emit("global_addr", dst, [name])
            return Val(dst)
        if node.kind == "float":
            ty = want if want is not None and want.kind == "float" else F64
            return self.const(float(node.value), ty)
        if node.kind == "bool":
            return self.const(1 if node.value else 0, I64)
        if node.kind == "none":
            return self.const(0, I64)
        ty = want if want is not None and want.kind == "int" else I64
        return self.const(int(node.value), ty)

    def expr_name(self, node: A.Name) -> Val:
        binding = self.lookup(node.ident)
        if isinstance(binding, Val):
            return binding
        if isinstance(binding, Slot):
            if binding.layout is not None:
                # A local holding a layout/type IS its storage, so it evaluates
                # to its address -- loading would read the first field instead.
                return Val(binding.ptr, binding.layout)
            dst = self.tmp(binding.type)
            self.emit("load", dst, [binding.ptr])
            return Val(dst, binding.layout)
        if node.ident in self.mod.consts:
            value, ty = self.mod.consts[node.ident]
            if isinstance(value, str):
                # A string constant names bytes in .rodata, so it evaluates to
                # their address -- `const` would try to encode the text.
                dst = self.tmp(PTR)
                self.emit("global_addr", dst, [self.mod.intern_string(value)])
                return Val(dst)
            return self.const(value, ty)

        # A function named in a value position is its address. For an imported
        # symbol this is the import thunk (`jmp [IAT]`), which is a correct and
        # callable address -- so an OS callback slot can be filled without the
        # language needing a function-pointer type.
        for key in ((f"{self.ns}::{node.ident}", node.ident) if self.ns
                    else (node.ident,)):
            if key in self.mod.signatures:
                dst = self.tmp(PTR)
                self.emit("global_addr", dst, [self.mod.symbols.get(key, key)])
                return Val(dst)

        raise self.err(f"undefined name {node.ident!r}", node)

    def expr_ns(self, node: A.NsAccess) -> Val:
        members = self.mod.enums.get(node.namespace)
        if members is None:
            raise self.err(f"unknown namespace {node.namespace!r}", node)
        if node.member not in members:
            raise self.err(
                f"{node.namespace!r} has no member {node.member!r}", node)
        return self.const(members[node.member], I64)

    @staticmethod
    def _power_of_two(value) -> int | None:
        if isinstance(value, int) and value > 0 and not (value & (value - 1)):
            return value.bit_length() - 1
        return None

    def _strength_reduce(self, node: A.Binary, lhs: Val) -> Val | None:
        """`x * 4` -> `shl x, 2`, and unsigned `/`/`%` by a power of two.

        Worth doing in the frontend rather than leaving to `peephole`: the o2
        preset is not usable here yet (`licm,sink` miscompiles), so a program
        built with no passes at all still wants an integer divide off its hot
        path -- `idiv` is tens of cycles against one for a shift.
        """
        shift = self._power_of_two(self.mod._fold(node.rhs))
        if shift is None or lhs.type.kind != "int":
            return None

        if node.op == "*":
            dst = self.tmp(lhs.type)
            amount = self.const(shift, lhs.type)
            self.emit("shl", dst, [lhs.ir, amount.ir])
            return Val(dst)

        if lhs.type.signed:
            # Signed `/` and `%` round toward zero, which a bare shift or mask
            # does not reproduce for negatives. Bias by (2^k - 1) when the
            # value is negative and the arithmetic comes out right for every
            # input -- four cheap ops against a multi-cycle idiv.
            if node.op not in ("/", "%"):
                return None
            bits = lhs.type.bits
            ty = lhs.type
            sign = self.tmp(ty)
            self.emit("sar", sign, [lhs.ir, self.const(bits - 1, ty).ir])
            bias = self.tmp(ty)
            self.emit("shr", bias, [sign, self.const(bits - shift, ty).ir])
            biased = self.tmp(ty)
            self.emit("iadd", biased, [lhs.ir, bias])

            if node.op == "/":
                dst = self.tmp(ty)
                self.emit("sar", dst, [biased, self.const(shift, ty).ir])
                return Val(dst)

            masked = self.tmp(ty)
            self.emit("iand", masked, [biased, self.const((1 << shift) - 1, ty).ir])
            dst = self.tmp(ty)
            self.emit("isub", dst, [masked, bias])
            return Val(dst)

        if node.op == "/":
            dst = self.tmp(lhs.type)
            amount = self.const(shift, lhs.type)
            self.emit("shr", dst, [lhs.ir, amount.ir])
            return Val(dst)
        if node.op == "%":
            dst = self.tmp(lhs.type)
            mask = self.const((1 << shift) - 1, lhs.type)
            self.emit("iand", dst, [lhs.ir, mask.ir])
            return Val(dst)
        return None

    def expr_binary(self, node: A.Binary) -> Val:
        op = node.op
        lhs = self.expr(node.lhs)
        if op in ("*", "/", "%"):
            reduced = self._strength_reduce(node, lhs)
            if reduced is not None:
                return reduced
        rhs = self.expr(node.rhs, want=lhs.type)
        ty = self.unify(lhs, rhs, node)
        lhs = self.coerce(lhs, ty, node)
        rhs = self.coerce(rhs, ty, node)

        if op in _CMP_SUFFIX:
            suffix = _CMP_SUFFIX[op]
            if ty.kind == "float":
                name = f"fcmp.{suffix}"
            elif not ty.signed and suffix in ("lt", "le", "gt", "ge"):
                name = f"icmp.u{suffix}"
            else:
                name = f"icmp.{suffix}"
            dst = self.tmp(I64)
            self.emit(name, dst, [lhs.ir, rhs.ir])
            return Val(dst)

        if ty.kind == "float":
            name = _FLOAT_BINOPS.get(op)
            if name is None:
                raise self.err(f"operator {op!r} is not defined for floats", node)
        elif op == "/":
            name = "idiv" if ty.signed else "udiv"
        elif op == "%":
            name = "irem" if ty.signed else "urem"
        elif op == ">>":
            name = "sar" if ty.signed else "shr"
        else:
            name = _INT_BINOPS.get(op)
            if name is None:
                raise self.err(f"unknown operator {op!r}", node)

        dst = self.tmp(ty)
        self.emit(name, dst, [lhs.ir, rhs.ir])
        return Val(dst)

    def expr_unary(self, node: A.Unary) -> Val:
        if node.op == "&":
            if isinstance(node.operand, A.Name):
                binding = self.lookup(node.operand.ident)
                if isinstance(binding, Slot):
                    return Val(binding.ptr, binding.layout)
            raise self.err("'&' needs an addressable variable", node)
        if node.op == "*":
            val = self.expr(node.operand)
            dst = self.tmp(I64)
            self.emit("load", dst, [val.ir])
            return Val(dst)

        val = self.expr(node.operand)
        if node.op == "-":
            dst = self.tmp(val.type)
            self.emit("fneg" if val.type.kind == "float" else "ineg", dst, [val.ir])
            return Val(dst)
        if node.op == "~":
            dst = self.tmp(val.type)
            self.emit("inot", dst, [val.ir])
            return Val(dst)
        if node.op == "!":
            zero = self.const(0, val.type)
            dst = self.tmp(I64)
            self.emit("icmp.eq", dst, [val.ir, zero.ir])
            return Val(dst)
        raise self.err(f"unknown unary operator {node.op!r}", node)

    def _resolve_callee(self, node: A.Call):
        """(symbol, signature, leading args) for a call site."""
        callee = node.callee

        if isinstance(callee, A.Name):
            name = callee.ident
            if name in self.mod.types:
                return None, None, []          # instantiation; handled by caller
            # Inside a module, an unqualified call means that module's own
            # function first -- `clear(ev)` in std::input is `input::clear`,
            # which emits as `input__clear`, not as a bare `clear`.
            if self.ns is not None:
                scoped = f"{self.ns}::{name}"
                if scoped in self.mod.signatures:
                    return (self.mod.symbols.get(scoped, scoped),
                            self.mod.signatures[scoped], [])
            return (self.mod.symbols.get(name, name),
                    self.mod.signatures.get(name), [])

        if isinstance(callee, A.NsAccess):
            ns, member = callee.namespace, callee.member
            qualified = f"{ns}::{member}"
            if member in self.mod.module_members.get(ns, ()):
                return (self.mod.symbols.get(qualified, qualified),
                        self.mod.signatures.get(qualified), [])
            ty = self.mod.types.get(ns)
            if ty is not None:
                decl = ty.methods.get(member)
                if decl is None:
                    raise self.err(f"type {ns!r} has no method {member!r}", node)
                if member not in ty.plain:
                    raise self.err(
                        f"{ns}::{member} is an instance method -- call it on a "
                        f"value, or declare it 'func plain'", node)
                return ty.symbol(member), decl, []
            recv = self.lookup(ns)
            if recv is None:
                raise self.err(f"unknown namespace or value {ns!r}", node)
            obj = self.expr_name(A.Name(node.line, node.col, ident=ns))
            if obj.layout is None or obj.layout not in self.mod.types:
                raise self.err(
                    f"{ns!r} is not an instance of a 'type', so {ns}::{member} "
                    "has no receiver", node)
            ty = self.mod.types[obj.layout]
            decl = ty.methods.get(member)
            if decl is None:
                raise self.err(
                    f"type {ty.name!r} has no method {member!r}", node)
            return ty.symbol(member), decl, [obj]

        raise self.err("only direct calls by name are supported", node)

    def expr_instantiate(self, name: str, node: A.Call) -> Val:
        ty = self.mod.types[name]
        slot = self._alloca(PTR)
        slot.ptr.type = PTR
        # The instance lives in this frame. Returning it, or storing it
        # somewhere outliving the call, is a dangling pointer.
        inst = self.tmp(PTR)
        self.emit("mov", inst, [slot.ptr])
        if ty.ctor is not None:
            args = [self.expr(a if not isinstance(a, tuple) else a[1]).ir
                    for a in node.args]
            self.emit("call", None, [ty.symbol("constructor"), inst, *args])
        return Val(inst, name)

    def expr_call(self, node: A.Call) -> Val:
        if isinstance(node.callee, A.Name) and node.callee.ident in self.mod.types:
            return self.expr_instantiate(node.callee.ident, node)

        name, sig, leading = self._resolve_callee(node)

        args: list = []
        for i, arg in enumerate(node.args):
            if isinstance(arg, tuple):          # named argument
                key, expr_node = arg
                if sig is None:
                    raise self.err(
                        f"named argument {key!r} needs a declared signature", node)
                idx = next((j for j, p in enumerate(sig.params) if p.name == key), None)
                if idx is None:
                    raise self.err(f"{name!r} has no parameter {key!r}", node)
                want = self._ir_type(sig.params[idx].type_name, node)
                args.append((idx, self.coerce(self.expr(expr_node, want), want, node)))
                continue
            want = None
            if sig is not None and i < len(sig.params):
                want = self._ir_type(sig.params[i].type_name, node)
            val = self.expr(arg, want=want)
            if want is not None:
                val = self.coerce(val, want, node)
            args.append((i, val))

        ordered = [*leading, *(v for _, v in sorted(args, key=lambda kv: kv[0]))]
        ret = I64 if sig is None else (
            None if T.is_void(sig.ret_type) else self._ir_type(sig.ret_type, node))
        layout = sig.ret_type if sig is not None and (
            sig.ret_type in self.mod.types or sig.ret_type in self.mod.layouts) else None
        dst = self.tmp(ret) if ret is not None else None
        self.emit("call", dst, [name, *[v.ir for v in ordered]])
        return Val(dst, layout) if dst is not None else self.const(0, I64)

    def expr_cast(self, node: A.Cast) -> Val:
        target = node.type_name
        if target in self.mod.layouts:
            val = self.expr(node.expr)
            return Val(val.ir, target)          # reinterpret memory as a layout
        val = self.expr(node.expr)
        return self.convert(val, self._ir_type(target, node), node)

    # ── addresses ────────────────────────────────────────────────────────
    def member_address(self, node: A.Member) -> tuple[IRValue, IRType]:
        base = self.expr(node.obj)
        if base.layout is None:
            raise self.err(
                "field access needs a layout -- interpret the pointer first "
                "(e.g. `buf as Header`)", node)
        lay = self.mod.layouts[base.layout]
        if node.field not in lay.offsets:
            raise self.err(f"layout {lay.name!r} has no field {node.field!r}", node)
        off = lay.offsets[node.field]
        size = lay.sizes[node.field]
        ty = lay.types.get(node.field)
        if ty is None:
            raise self.err(
                f"field {node.field!r} is {size} bytes -- only 1/2/4/8 can be "
                "loaded directly; take its address instead", node)
        addr = self.tmp(PTR)
        self.emit("gep", addr, [base.ir, off])
        return addr, ty

    def index_address(self, node: A.Index) -> tuple[IRValue, IRType]:
        base = self.expr(node.obj)
        idx = self.coerce(self.expr(node.index), I64, node)
        addr = self.tmp(PTR)
        self.emit("gep", addr, [base.ir, idx.ir])
        return addr, T.SCALARS["u8"]

    # ── types ────────────────────────────────────────────────────────────
    def sizeof(self, name: str, node: A.Node) -> int:
        if name in self.mod.layouts:
            return self.mod.layouts[name].size
        size = T.layout_field_size(name)
        if size is None:
            raise self.err(f"sizeof: unknown type {name!r}", node)
        return size

    def unify(self, lhs: Val, rhs: Val, node: A.Node) -> IRType:
        lt, rt = lhs.type, rhs.type
        if lt.name == rt.name:
            return lt
        if lt.kind == "float" or rt.kind == "float":
            return F64 if F64.name in (lt.name, rt.name) else F32
        if lt.kind == "ptr":
            return lt
        if rt.kind == "ptr":
            return rt
        return lt if lt.size_bytes >= rt.size_bytes else rt

    def coerce(self, val: Val, ty: IRType, node: A.Node) -> Val:
        if val.type.name == ty.name:
            return val
        return self.convert(val, ty, node)

    def convert(self, val: Val, ty: IRType, node: A.Node) -> Val:
        src = val.type
        if src.name == ty.name:
            return val
        dst = self.tmp(ty)

        if src.kind == "float" and ty.kind == "float":
            self.emit("fpext" if ty.bits > src.bits else "fptrunc", dst, [val.ir])
        elif src.kind == "float":
            self.emit("fptosi", dst, [val.ir])
        elif ty.kind == "float":
            self.emit("sitofp", dst, [val.ir])
        elif src.kind == "ptr" or ty.kind == "ptr":
            self.emit("mov", dst, [val.ir])
        elif ty.bits > src.bits:
            self.emit("sext" if src.signed else "zext", dst, [val.ir])
        elif ty.bits < src.bits:
            self.emit("trunc", dst, [val.ir])
        else:
            self.emit("mov", dst, [val.ir])     # same width, different signedness
        return Val(dst, val.layout)

    def truth(self, val: Val) -> Val:
        """A condition operand for br.t: nonzero is true."""
        if val.type.kind == "int" and val.type.name == "i64":
            return val
        return self.coerce(val, I64, A.Node())


def emit_module(mod: A.Module, src: str) -> IRModule:
    return ModuleEmitter(src).run(mod)


__all__ = ["ModuleEmitter", "emit_module"]
