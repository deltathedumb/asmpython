"""Semantic-analysis pass.

Runs after parsing, before codegen. Catches anything the parser can't:
- Undefined variable references
- Undefined function calls / wrong argument count
- `break` / `continue` outside a loop
- `return` outside a function
- Calls to known builtins with wrong shape (e.g. print() with zero args)

It also performs a small amount of light "type" tracking: enough to reject
obviously-wrong things like `print(a_function_name)` or string-as-int math.
Anything we can't decide statically gets a pass (Python is dynamic; we only
flag what's clearly wrong).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Optional

from . import ast_nodes as A
from . import stdlib
from .errors import SemaError


# Builtins we accept. Values describe required arg-count range.
BUILTINS: dict[str, tuple[int, int]] = {
    "print": (0, 64),    # 0 args = just a newline; >0 = space-separated
    "len":   (1, 1),
    "int":   (1, 1),
    "float": (1, 1),
    "str":   (1, 1),
    "input": (0, 1),
}


@dataclass
class FuncSig:
    name: str
    arity: int
    pos: A.SourcePos


@dataclass
class Scope:
    """Tracks defined names and their last-known static type.

    Type tracking is simple: when a name is assigned, we record the static
    type of the RHS. Reassigning to a different type "wins" — we just
    overwrite. This is enough to dispatch print() correctly for the common
    cases (`name = input()` then `print(name)`), without trying to be a real
    type checker.
    """
    types: dict[str, str] = field(default_factory=dict)

    @property
    def names(self):
        # Back-compat for the membership checks elsewhere.
        return self.types.keys()

    def add(self, name: str, ty: str = "int") -> None:
        self.types[name] = ty

    def __contains__(self, name: str) -> bool:
        return name in self.types


def _load_module(name: str) -> dict:
    """Load `compyle.stdlib.<name>` and return its BINDINGS dict."""
    try:
        mod = importlib.import_module(f"compyle.stdlib.{name}")
    except ImportError as e:
        raise SemaError(f"no such module: {name!r}") from e
    if not hasattr(mod, "BINDINGS"):
        raise SemaError(f"stdlib module {name!r} has no BINDINGS dict")
    return mod.BINDINGS


class SemaAnalyzer:
    def __init__(self, mod: A.Module) -> None:
        self.mod = mod
        self.funcs: dict[str, FuncSig] = {}
        self.loop_depth = 0
        self.in_function: Optional[str] = None
        # Imported FFI: bindings either bound under a module prefix or
        # lifted directly into the namespace via from-import.
        self.imported_modules: dict[str, dict] = {}
        self.ffi_funcs: dict[str, stdlib.Func] = {}
        self.ffi_consts: dict[str, stdlib.Const] = {}

    # ---- entry --------------------------------------------------------------

    def analyze(self) -> None:
        # First pass: collect function signatures so forward references resolve.
        for f in self.mod.funcs:
            if f.name in self.funcs:
                raise SemaError(f"function {f.name!r} redefined", f.pos)
            if f.name in BUILTINS:
                raise SemaError(
                    f"cannot redefine builtin {f.name!r}", f.pos,
                )
            self.funcs[f.name] = FuncSig(name=f.name, arity=len(f.params), pos=f.pos)

        # Function bodies: each has its own scope, seeded with params.
        for f in self.mod.funcs:
            self.in_function = f.name
            scope = Scope()
            for p in f.params:
                scope.add(p, "int")   # all func params are ints in our model
            self._check_block(f.body, scope)
            self.in_function = None

        # Top-level body.
        self._check_block(self.mod.body, Scope())

        # Hand the resolved FFI tables to codegen via the Module.
        self.mod.imported_modules = self.imported_modules
        self.mod.ffi_funcs = self.ffi_funcs
        self.mod.ffi_consts = self.ffi_consts

    # ---- helpers ------------------------------------------------------------

    def _check_block(self, stmts: list, scope: Scope) -> None:
        for s in stmts:
            self._check_stmt(s, scope)

    def _check_stmt(self, s, scope: Scope) -> None:
        if isinstance(s, A.Pass):
            return
        if isinstance(s, A.Assign):
            if isinstance(s.value, A.FString):
                raise SemaError(
                    "f-strings are only supported as arguments to print() right now",
                    s.value.pos,
                )
            self._check_expr(s.value, scope)
            scope.add(s.target, A.expr_type(s.value))
            return
        if isinstance(s, A.AugAssign):
            if s.target not in scope.names:
                raise SemaError(
                    f"augmented assignment to undefined variable {s.target!r}",
                    s.pos,
                )
            self._check_expr(s.value, scope)
            return
        if isinstance(s, A.Return):
            if self.in_function is None:
                raise SemaError("'return' outside of a function", s.pos)
            if s.value is not None:
                self._check_expr(s.value, scope)
            return
        if isinstance(s, A.If):
            self._check_expr(s.test, scope)
            # Branches see the outer scope; assignments inside branches leak
            # out (Python semantics). We model that by sharing the scope.
            self._check_block(s.then, scope)
            self._check_block(s.orelse, scope)
            return
        if isinstance(s, A.While):
            self._check_expr(s.test, scope)
            self.loop_depth += 1
            try:
                self._check_block(s.body, scope)
            finally:
                self.loop_depth -= 1
            return
        if isinstance(s, A.For):
            if s.iter is not None:
                self._check_expr(s.iter, scope)
                if A.expr_type(s.iter) != "list":
                    raise SemaError(
                        "compyle 'for' iterates over range() or a list",
                        s.pos,
                    )
            else:
                for arg in s.range_args:
                    self._check_expr(arg, scope)
            scope.add(s.var, "int")
            self.loop_depth += 1
            try:
                self._check_block(s.body, scope)
            finally:
                self.loop_depth -= 1
            return
        if isinstance(s, A.Break):
            if self.loop_depth == 0:
                raise SemaError("'break' outside a loop", s.pos)
            return
        if isinstance(s, A.Continue):
            if self.loop_depth == 0:
                raise SemaError("'continue' outside a loop", s.pos)
            return
        if isinstance(s, A.Import):
            try:
                bindings = _load_module(s.module)
            except SemaError as e:
                # Reattach the position.
                raise SemaError(str(e.message), s.pos) from None
            self.imported_modules[s.module] = bindings
            # Make `math` a known name in scope (as a dummy int) so `math.x`
            # parses cleanly past the Name lookup.
            scope.add(s.module, "module")
            return
        if isinstance(s, A.FromImport):
            try:
                bindings = _load_module(s.module)
            except SemaError as e:
                raise SemaError(str(e.message), s.pos) from None
            for name in s.names:
                if name not in bindings:
                    raise SemaError(
                        f"module {s.module!r} has no {name!r}",
                        s.pos,
                    )
                b = bindings[name]
                if isinstance(b, stdlib.Func):
                    self.ffi_funcs[name] = b
                else:
                    self.ffi_consts[name] = b
                    scope.add(name, b.ty)
            return
        if isinstance(s, A.ExprStmt):
            self._check_expr(s.expr, scope)
            return
        if isinstance(s, A.IndexAssign):
            self._check_expr(s.target.obj, scope)
            self._check_expr(s.target.index, scope)
            if A.expr_type(s.target.obj) != "list":
                raise SemaError("can only assign to list[index]", s.pos)
            self._check_expr(s.value, scope)
            return
        raise SemaError(f"internal: unhandled stmt {type(s).__name__}", getattr(s, "pos", None))

    def _check_expr(self, e, scope: Scope) -> None:
        if isinstance(e, (A.IntLit, A.FloatLit, A.StrLit)):
            return
        if isinstance(e, A.Name):
            if e.name in self.ffi_consts:
                e.inferred_type = self.ffi_consts[e.name].ty
                return
            if e.name not in scope:
                raise SemaError(f"undefined variable {e.name!r}", e.pos)
            e.inferred_type = scope.types[e.name]
            return
        if isinstance(e, A.UnaryOp):
            self._check_expr(e.operand, scope)
            return
        if isinstance(e, A.BinOp):
            self._check_expr(e.left, scope)
            self._check_expr(e.right, scope)
            lt, rt = A.expr_type(e.left), A.expr_type(e.right)
            # Numeric-only ops; reject strings/lists.
            for side, t in (("left", lt), ("right", rt)):
                if t not in ("int", "float"):
                    raise SemaError(
                        f"unsupported operand type for {e.op}: {t}",
                        e.pos,
                    )
            # Bitwise / shift can't take floats.
            if e.op in ("&", "|", "^", "<<", ">>"):
                if "float" in (lt, rt):
                    raise SemaError(
                        f"bitwise/shift operator {e.op!r} requires int operands",
                        e.pos,
                    )
            # Floor div and mod with mixed types: allow, treat as float.
            return
        if isinstance(e, A.Compare):
            for op in e.operands:
                self._check_expr(op, scope)
            return
        if isinstance(e, A.BoolOp):
            self._check_expr(e.left, scope)
            self._check_expr(e.right, scope)
            return
        if isinstance(e, A.Call):
            self._check_call(e, scope)
            return
        if isinstance(e, A.ListLit):
            for el in e.elems:
                self._check_expr(el, scope)
                if A.expr_type(el) != "int":
                    raise SemaError(
                        "list elements must be ints (floats not supported yet)",
                        getattr(el, "pos", e.pos),
                    )
            return
        if isinstance(e, A.Subscript):
            self._check_expr(e.obj, scope)
            self._check_expr(e.index, scope)
            obj_t = A.expr_type(e.obj)
            if obj_t == "list":
                e.inferred_type = "int"
            elif obj_t == "str":
                # str indexing isn't implemented yet
                raise SemaError("string indexing not supported yet", e.pos)
            else:
                raise SemaError(f"cannot index a {obj_t}", e.pos)
            return
        if isinstance(e, A.FString):
            for seg in e.segments:
                self._check_expr(seg, scope)
                t = A.expr_type(seg)
                if t not in ("int", "float", "str"):
                    raise SemaError(
                        f"f-string segment cannot be a {t}",
                        getattr(seg, "pos", e.pos),
                    )
            return
        if isinstance(e, A.Attr):
            # Special-case module attribute: math.pi, math.sqrt(...).
            if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
                bindings = self.imported_modules[e.obj.name]
                if e.name not in bindings:
                    raise SemaError(
                        f"module {e.obj.name!r} has no {e.name!r}",
                        e.pos,
                    )
                b = bindings[e.name]
                if isinstance(b, stdlib.Func):
                    e.inferred_type = b.ret_type
                else:
                    e.inferred_type = b.ty
                return
            # General attribute access isn't otherwise supported yet.
            raise SemaError(
                f"attribute access {e.name!r} not supported here",
                e.pos,
            )
        if isinstance(e, A.MethodCall):
            # Module function call: math.sqrt(x), math.pow(a, b).
            if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
                bindings = self.imported_modules[e.obj.name]
                if e.method not in bindings or not isinstance(bindings[e.method], stdlib.Func):
                    raise SemaError(
                        f"module {e.obj.name!r} has no callable {e.method!r}",
                        e.pos,
                    )
                fn = bindings[e.method]
                self._check_ffi_call(fn, e.args, e.pos, scope, label=f"{e.obj.name}.{e.method}")
                e.inferred_type = fn.ret_type
                return
            self._check_expr(e.obj, scope)
            for a in e.args:
                self._check_expr(a, scope)
            obj_t = A.expr_type(e.obj)
            if obj_t == "list":
                if e.method == "append":
                    if len(e.args) != 1:
                        raise SemaError(
                            f"list.append() takes 1 argument, got {len(e.args)}", e.pos,
                        )
                    if A.expr_type(e.args[0]) != "int":
                        raise SemaError("list.append() requires an int", e.pos)
                    e.inferred_type = "int"   # returns None ~ 0
                elif e.method == "pop":
                    if e.args:
                        raise SemaError("list.pop() takes no arguments", e.pos)
                    e.inferred_type = "int"
                else:
                    raise SemaError(f"list has no method {e.method!r}", e.pos)
            else:
                raise SemaError(f"{obj_t} has no method {e.method!r}", e.pos)
            return
        raise SemaError(f"internal: unhandled expr {type(e).__name__}", getattr(e, "pos", None))

    def _check_ffi_call(self, fn: stdlib.Func, args: list, pos, scope: Scope, *, label: str) -> None:
        """Validate an FFI call's arity and arg types. Performs implicit
        int->float promotion at the call site (so the user can write
        `math.sqrt(4)` without writing `4.0`)."""
        if len(args) != len(fn.arg_types):
            raise SemaError(
                f"{label}() takes {len(fn.arg_types)} argument(s), got {len(args)}",
                pos,
            )
        for i, (a, want) in enumerate(zip(args, fn.arg_types)):
            self._check_expr(a, scope)
            got = A.expr_type(a)
            if got == want:
                continue
            # Allow int -> float promotion.
            if want == "float" and got == "int":
                continue
            raise SemaError(
                f"{label}() argument {i+1}: expected {want}, got {got}",
                pos,
            )

    def _check_call(self, e: A.Call, scope: Scope) -> None:
        # f-strings outside print() aren't supported yet (would need runtime
        # string concat). Flag them now so the user gets a clean message
        # instead of a codegen surprise later.
        if e.func != "print":
            for a in e.args:
                if isinstance(a, A.FString):
                    raise SemaError(
                        "f-strings are only supported as arguments to print() right now",
                        getattr(a, "pos", e.pos),
                    )
        if e.func in BUILTINS:
            lo, hi = BUILTINS[e.func]
            if not (lo <= len(e.args) <= hi):
                if lo == hi:
                    raise SemaError(
                        f"{e.func}() takes {lo} argument(s), got {len(e.args)}",
                        e.pos,
                    )
                raise SemaError(
                    f"{e.func}() takes {lo}-{hi} arguments, got {len(e.args)}",
                    e.pos,
                )
            for a in e.args:
                self._check_expr(a, scope)
            # Set the static return type so codegen knows how to interpret it.
            e.inferred_type = {
                "print": "int",
                "len":   "int",
                "int":   "int",
                "float": "float",
                "str":   "str",
                "input": "str",
            }[e.func]
            # Argument-type sanity for builtins that care.
            if e.func == "len":
                t = A.expr_type(e.args[0])
                if t not in ("str", "list"):
                    raise SemaError("len() requires a string or list argument", e.pos)
            elif e.func == "int":
                t = A.expr_type(e.args[0])
                if t not in ("str", "float", "int"):
                    raise SemaError("int() requires str / float / int", e.pos)
            elif e.func == "float":
                t = A.expr_type(e.args[0])
                if t not in ("str", "int", "float"):
                    raise SemaError("float() requires str / int / float", e.pos)
            elif e.func == "str":
                t = A.expr_type(e.args[0])
                if t not in ("int", "float", "str"):
                    raise SemaError("str() requires int / float / str", e.pos)
            return
        if e.func in self.funcs:
            sig = self.funcs[e.func]
            if len(e.args) != sig.arity:
                raise SemaError(
                    f"{e.func}() takes {sig.arity} argument(s), got {len(e.args)}",
                    e.pos,
                )
            for a in e.args:
                self._check_expr(a, scope)
            # User-defined functions always return int in our model.
            e.inferred_type = "int"
            return
        if e.func in self.ffi_funcs:
            fn = self.ffi_funcs[e.func]
            self._check_ffi_call(fn, e.args, e.pos, scope, label=e.func)
            e.inferred_type = fn.ret_type
            return
        raise SemaError(f"undefined function {e.func!r}", e.pos)


def analyze(mod: A.Module) -> None:
    SemaAnalyzer(mod).analyze()
