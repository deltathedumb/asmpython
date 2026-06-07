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
    "print": (0, 64),  # 0 args = just a newline; >0 = space-separated
    "len": (1, 1),
    "int": (1, 1),
    "float": (1, 1),
    "str": (1, 1),
    "input": (0, 1),
}


@dataclass
class FuncSig:
    name: str
    arity: int
    pos: A.SourcePos
    # Number of trailing parameters that have default values; required
    # arity is `arity - n_defaults`. Caller may omit up to n_defaults of them.
    n_defaults: int = 0


@dataclass
class ClassSig:
    """Compile-time information about a class.

    `methods` maps method name -> FuncSig (where arity counts `self`).
    Resolution walks `parent` chains until a method is found.
    """

    name: str
    parent: Optional[str]
    methods: dict[str, FuncSig] = field(default_factory=dict)
    pos: A.SourcePos = None  # type: ignore


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
    # For names typed "list", element type — "int" by default, may be "str"
    # or "float". Mixed-type lists wait on a tagged-value runtime.
    list_el_types: dict[str, str] = field(default_factory=dict)

    @property
    def names(self):
        # Back-compat for the membership checks elsewhere.
        return self.types.keys()

    def add(self, name: str, ty: str = "int", *, el_type: str | None = None) -> None:
        self.types[name] = ty
        if ty == "list" and el_type is not None:
            self.list_el_types[name] = el_type

    def __contains__(self, name: str) -> bool:
        return name in self.types


def _load_module(name: str) -> dict:
    """Load `serpent.stdlib.<name>` and return its BINDINGS dict."""
    try:
        mod = importlib.import_module(f"serpent.stdlib.{name}")
    except ImportError as e:
        raise SemaError(f"no such module: {name!r}") from e
    if not hasattr(mod, "BINDINGS"):
        raise SemaError(f"stdlib module {name!r} has no BINDINGS dict")
    return mod.BINDINGS


class SemaAnalyzer:
    def __init__(self, mod: A.Module) -> None:
        self.mod = mod
        self.funcs: dict[str, FuncSig] = {}
        self.classes: dict[str, ClassSig] = {}
        self.loop_depth = 0
        self.in_function: Optional[str] = None
        # Imported FFI: bindings either bound under a module prefix or
        # lifted directly into the namespace via from-import.
        self.imported_modules: dict[str, dict] = {}
        self.ffi_funcs: dict[str, stdlib.Func] = {}
        self.ffi_consts: dict[str, stdlib.Const] = {}

    def _resolve_method(
        self, class_name: str, method: str
    ) -> Optional[tuple[str, FuncSig]]:
        """Walk parent chain to find the class that owns `method`.

        Returns (owner_class_name, FuncSig) or None.
        """
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls = self.classes.get(cur)
            if cls is None:
                return None
            if method in cls.methods:
                return cur, cls.methods[method]
            cur = cls.parent
        return None

    # ---- entry --------------------------------------------------------------

    def analyze(self) -> None:
        # First pass: collect function signatures so forward references resolve.
        for f in self.mod.funcs:
            if f.name in self.funcs:
                raise SemaError(f"function {f.name!r} redefined", f.pos)
            if f.name in BUILTINS:
                raise SemaError(
                    f"cannot redefine builtin {f.name!r}",
                    f.pos,
                )
            self.funcs[f.name] = FuncSig(
                name=f.name,
                arity=len(f.params),
                n_defaults=sum(1 for d in f.defaults if d is not None),
                pos=f.pos,
            )

        # Collect class signatures so methods + constructor calls resolve.
        for c in self.mod.classes:
            if c.name in self.classes or c.name in self.funcs or c.name in BUILTINS:
                raise SemaError(
                    f"class name {c.name!r} collides with existing name", c.pos
                )
            sig = ClassSig(name=c.name, parent=c.parent, pos=c.pos)
            for m in c.methods:
                if not m.params or m.params[0] != "self":
                    raise SemaError(
                        f"method {c.name}.{m.name!r} must take 'self' as its first parameter",
                        m.pos,
                    )
                sig.methods[m.name] = FuncSig(
                    name=m.name,
                    arity=len(m.params),
                    n_defaults=sum(1 for d in m.defaults if d is not None),
                    pos=m.pos,
                )
            self.classes[c.name] = sig

        # Validate parents exist and aren't cyclic.
        for c in self.mod.classes:
            if c.parent is not None:
                if c.parent not in self.classes:
                    raise SemaError(f"unknown parent class {c.parent!r}", c.pos)
                # Cycle check.
                seen, cur = {c.name}, c.parent
                while cur is not None:
                    if cur in seen:
                        raise SemaError(
                            f"inheritance cycle involving {c.name!r}", c.pos
                        )
                    seen.add(cur)
                    cur = self.classes[cur].parent

        # Function bodies: each has its own scope, seeded with params.
        # If a param has a default literal, infer its type from the default
        # (so `def greet(p="hi")` makes p a str in the body).
        for f in self.mod.funcs:
            self.in_function = f.name
            scope = Scope()
            for i, p in enumerate(f.params):
                ty = "int"
                if i < len(f.defaults) and f.defaults[i] is not None:
                    ty = A.expr_type(f.defaults[i])
                scope.add(p, ty)
            self._check_block(f.body, scope)
            self.in_function = None

        # Method bodies: `self` is typed as the instance of its class.
        for c in self.mod.classes:
            for m in c.methods:
                self.in_function = f"{c.name}__{m.name}"
                scope = Scope()
                scope.add("self", f"instance:{c.name}")
                for i, p in enumerate(m.params[1:], start=1):
                    ty = "int"
                    if i < len(m.defaults) and m.defaults[i] is not None:
                        ty = A.expr_type(m.defaults[i])
                    scope.add(p, ty)
                self._check_block(m.body, scope)
                self.in_function = None

        # Top-level body.
        self._check_block(self.mod.body, Scope())

        # Hand resolved tables to codegen via the Module.
        self.mod.imported_modules = self.imported_modules
        self.mod.ffi_funcs = self.ffi_funcs
        self.mod.ffi_consts = self.ffi_consts
        # Codegen needs to look up methods by class chain for dispatch.
        self.mod.classes_sig = self.classes

    # ---- helpers ------------------------------------------------------------

    def _check_block(self, stmts: list, scope: Scope) -> None:
        for s in stmts:
            self._check_stmt(s, scope)

    def _list_el_type(self, e, scope: Scope) -> str:
        """Element type of a list-valued expression. 'int' if unknown."""
        if isinstance(e, A.ListLit):
            return e.el_type
        if isinstance(e, A.Name):
            return scope.list_el_types.get(e.name, "int")
        return "int"

    def _check_stmt(self, s, scope: Scope) -> None:
        if isinstance(s, A.Pass):
            return
        if isinstance(s, A.Assign):
            self._check_expr(s.value, scope)
            t = A.expr_type(s.value)
            if t == "list":
                scope.add(s.target, t, el_type=self._list_el_type(s.value, scope))
            else:
                scope.add(s.target, t)
            return
        if isinstance(s, A.TupleAssign):
            if len(s.targets) != len(s.values):
                raise SemaError(
                    f"tuple assign expects {len(s.targets)} values, got {len(s.values)}",
                    s.pos,
                )
            for v in s.values:
                self._check_expr(v, scope)
            for t, v in zip(s.targets, s.values):
                vt = A.expr_type(v)
                # v1 limitation: only int variables (same restriction as
                # standard Assign for mixed types -- when [[boxed-values]]
                # land, this widens.)
                if vt != "int":
                    raise SemaError(
                        f"tuple assign target {t!r}: only int values are supported yet (got {vt})",
                        s.pos,
                    )
                scope.add(t, vt)
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
                it_t = A.expr_type(s.iter)
                if it_t == "list":
                    scope.add(s.var, self._list_el_type(s.iter, scope))
                elif it_t == "dict":
                    # Iterating a dict yields its keys (strings).
                    scope.add(s.var, "str")
                elif it_t == "str":
                    # Each iteration yields a fresh 1-char str.
                    scope.add(s.var, "str")
                else:
                    raise SemaError(
                        "serpent 'for' iterates over range(), list, dict, or str",
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
            # Dotted path: bind the leading segment ("os.path" -> "os"). Real
            # submodule lookup is post-bootstrap.
            top_name = s.module.split(".")[0]
            try:
                bindings = _load_module(top_name)
            except SemaError:
                # Module isn't in serpent's stdlib registry — accept the
                # statement as a parser-level no-op so source that uses
                # standard CPython modules can still be checked. The name
                # becomes a dummy in scope; any subsequent `x.attr` lookup
                # will still error at the attribute resolution step.
                scope.add(top_name, "module")
                return
            self.imported_modules[top_name] = bindings
            # Make `math` a known name in scope (as a dummy int) so `math.x`
            # parses cleanly past the Name lookup.
            scope.add(top_name, "module")
            return
        if isinstance(s, A.FromImport):
            # Relative import or unknown module: accept the syntax and bind
            # each imported name as a dummy int. Self-host needs every source
            # file to *parse*; real cross-file resolution comes later.
            if s.level > 0 or not s.module:
                for name in s.names:
                    scope.add(name, "int")
                return
            try:
                bindings = _load_module(s.module)
            except SemaError:
                # Unknown absolute module (e.g. `from os import getcwd`).
                # Bind each name as a dummy; subsequent calls will error at
                # the call site if the name isn't usable.
                for name in s.names:
                    scope.add(name, "int")
                return
            for name in s.names:
                if name not in bindings:
                    # Unknown binding inside a known module — accept as a
                    # dummy so source can still parse (mirrors the
                    # unknown-module fallback above).
                    scope.add(name, "int")
                    continue
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
            obj_t = A.expr_type(s.target.obj)
            self._check_expr(s.value, scope)
            value_t = A.expr_type(s.value)
            if obj_t == "list":
                el_t = self._list_el_type(s.target.obj, scope)
                if value_t != el_t:
                    raise SemaError(
                        f"list[i] = v: list element type is {el_t}, got {value_t}",
                        s.pos,
                    )
            elif obj_t == "dict":
                if A.expr_type(s.target.index) != "str":
                    raise SemaError("dict keys must be strings", s.pos)
                if value_t != "int":
                    raise SemaError("dict[k] = v requires v to be int", s.pos)
            else:
                raise SemaError(f"cannot index a {obj_t}", s.pos)
            return
        if isinstance(s, A.AttrAssign):
            self._check_expr(s.obj, scope)
            obj_t = A.expr_type(s.obj)
            if not obj_t.startswith("instance:"):
                raise SemaError(
                    f"cannot assign attribute on {obj_t}",
                    s.pos,
                )
            self._check_expr(s.value, scope)
            value_t = A.expr_type(s.value)
            if value_t != "int":
                raise SemaError(
                    "instance attribute values must be int (other types not supported yet)",
                    s.pos,
                )
            return
        if isinstance(s, A.Try):
            self._check_block(s.body, scope)
            if s.bind_name is not None:
                scope.add(s.bind_name, "str")
            self._check_block(s.handler, scope)
            return
        if isinstance(s, A.Raise):
            self._check_expr(s.value, scope)
            if A.expr_type(s.value) != "str":
                raise SemaError("raise requires a string message", s.pos)
            return
        raise SemaError(
            f"internal: unhandled stmt {type(s).__name__}", getattr(s, "pos", None)
        )

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
            if e.inferred_type == "list":
                e.list_el_type = scope.list_el_types.get(e.name, "int")
            return
        if isinstance(e, A.UnaryOp):
            self._check_expr(e.operand, scope)
            return
        if isinstance(e, A.BinOp):
            self._check_expr(e.left, scope)
            self._check_expr(e.right, scope)
            lt, rt = A.expr_type(e.left), A.expr_type(e.right)
            # String operations: + concatenates two strings; * repeats a string
            # by an int count. Anything else involving strings is rejected.
            if "str" in (lt, rt):
                if e.op == "+" and lt == "str" and rt == "str":
                    return
                if e.op == "*" and (
                    (lt == "str" and rt == "int") or (lt == "int" and rt == "str")
                ):
                    return
                raise SemaError(
                    f"unsupported operand type for {e.op}: {lt} {e.op} {rt}",
                    e.pos,
                )
            # Numeric-only ops; reject lists/dicts/instances.
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
            return
        if isinstance(e, A.Compare):
            for op in e.operands:
                self._check_expr(op, scope)
            for i, op in enumerate(e.ops):
                lt = A.expr_type(e.operands[i])
                rt = A.expr_type(e.operands[i + 1])
                if op in ("in", "not in"):
                    # Only str-in-str is wired up so far.
                    if lt != "str" or rt != "str":
                        raise SemaError(
                            f"'{op}' only supported on strings (got {lt} {op} {rt})",
                            e.pos,
                        )
                    continue
                if op in ("is", "is not"):
                    # serpent has no `None`-as-distinct-value yet. `x is None`
                    # therefore lowers to `x == 0`. Accept any operand types
                    # — the comparison happens at the raw 8-byte level.
                    continue
                if "str" in (lt, rt):
                    if op not in ("==", "!=", "<", "<=", ">", ">="):
                        raise SemaError(
                            f"string comparison does not support {op!r}",
                            e.pos,
                        )
                    if lt != "str" or rt != "str":
                        raise SemaError(
                            f"cannot compare {lt} and {rt} with {op!r}",
                            e.pos,
                        )
            return
        if isinstance(e, A.BoolOp):
            self._check_expr(e.left, scope)
            self._check_expr(e.right, scope)
            return
        if isinstance(e, A.Call):
            self._check_call(e, scope)
            return
        if isinstance(e, A.ListLit):
            seen: str | None = None
            for el in e.elems:
                self._check_expr(el, scope)
                et = A.expr_type(el)
                if et not in ("int", "str", "float"):
                    raise SemaError(
                        f"list element of type {et} is not supported yet",
                        getattr(el, "pos", e.pos),
                    )
                if seen is None:
                    seen = et
                elif seen != et:
                    raise SemaError(
                        f"mixed list element types ({seen} and {et}); "
                        "mixed-type lists need a tagged-value runtime, not yet implemented",
                        getattr(el, "pos", e.pos),
                    )
            # Empty literal stays "?" until the first append pins the type.
            e.el_type = seen if seen is not None else "?"
            return
        if isinstance(e, A.DictLit):
            for k in e.keys:
                self._check_expr(k, scope)
                if A.expr_type(k) != "str":
                    raise SemaError(
                        "dict keys must be strings (other types not supported yet)",
                        getattr(k, "pos", e.pos),
                    )
            for v in e.values:
                self._check_expr(v, scope)
                if A.expr_type(v) != "int":
                    raise SemaError(
                        "dict values must be ints (other types not supported yet)",
                        getattr(v, "pos", e.pos),
                    )
            return
        if isinstance(e, A.Subscript):
            self._check_expr(e.obj, scope)
            obj_t = A.expr_type(e.obj)
            if isinstance(e.index, A.Slice):
                if obj_t != "str":
                    raise SemaError(f"slicing not supported on {obj_t}", e.pos)
                if e.index.start is not None:
                    self._check_expr(e.index.start, scope)
                    if A.expr_type(e.index.start) != "int":
                        raise SemaError("slice start must be an int", e.pos)
                if e.index.stop is not None:
                    self._check_expr(e.index.stop, scope)
                    if A.expr_type(e.index.stop) != "int":
                        raise SemaError("slice stop must be an int", e.pos)
                e.inferred_type = "str"
                return
            self._check_expr(e.index, scope)
            if obj_t == "list":
                e.inferred_type = self._list_el_type(e.obj, scope)
            elif obj_t == "dict":
                if A.expr_type(e.index) != "str":
                    raise SemaError("dict keys must be strings", e.pos)
                e.inferred_type = "int"
            elif obj_t == "str":
                if A.expr_type(e.index) != "int":
                    raise SemaError("string index must be an int", e.pos)
                e.inferred_type = "str"
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
            # Instance field access: self.x, point.x — typed as int for v1
            # (all attribute values are int, since instances use a str->int dict).
            self._check_expr(e.obj, scope)
            obj_t = A.expr_type(e.obj)
            if obj_t.startswith("instance:"):
                e.inferred_type = "int"
                return
            raise SemaError(
                f"attribute access {e.name!r} not supported on {obj_t}",
                e.pos,
            )
        if isinstance(e, A.MethodCall):
            # Module function call: math.sqrt(x), math.pow(a, b).
            if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
                bindings = self.imported_modules[e.obj.name]
                if e.method not in bindings or not isinstance(
                    bindings[e.method], stdlib.Func
                ):
                    raise SemaError(
                        f"module {e.obj.name!r} has no callable {e.method!r}",
                        e.pos,
                    )
                fn = bindings[e.method]
                self._check_ffi_call(
                    fn, e.args, e.pos, scope, label=f"{e.obj.name}.{e.method}"
                )
                e.inferred_type = fn.ret_type
                return
            self._check_expr(e.obj, scope)
            for a in e.args:
                self._check_expr(a, scope)
            obj_t = A.expr_type(e.obj)
            if obj_t == "list":
                el_t = self._list_el_type(e.obj, scope)
                if e.method == "append":
                    if len(e.args) != 1:
                        raise SemaError(
                            f"list.append() takes 1 argument, got {len(e.args)}",
                            e.pos,
                        )
                    arg_t = A.expr_type(e.args[0])
                    if arg_t not in ("int", "str", "float"):
                        raise SemaError(
                            f"list.append() element of type {arg_t} not supported",
                            e.pos,
                        )
                    if el_t == "?":
                        # First append on an empty literal — pin the element type.
                        if isinstance(e.obj, A.Name):
                            scope.list_el_types[e.obj.name] = arg_t
                            e.obj.list_el_type = arg_t
                        el_t = arg_t
                    elif arg_t != el_t:
                        raise SemaError(
                            f"list.append() expected {el_t}, got {arg_t}",
                            e.pos,
                        )
                    e.inferred_type = "int"  # returns None ~ 0
                elif e.method == "pop":
                    if e.args:
                        raise SemaError("list.pop() takes no arguments", e.pos)
                    e.inferred_type = el_t if el_t != "?" else "int"
                else:
                    raise SemaError(f"list has no method {e.method!r}", e.pos)
            elif obj_t == "dict":
                if e.method == "get":
                    if len(e.args) != 2:
                        raise SemaError(
                            "dict.get() requires (key, default) — we don't model None yet",
                            e.pos,
                        )
                    if A.expr_type(e.args[0]) != "str":
                        raise SemaError("dict.get() key must be a str", e.pos)
                    if A.expr_type(e.args[1]) != "int":
                        raise SemaError("dict.get() default must be an int", e.pos)
                    e.inferred_type = "int"
                elif e.method == "contains":
                    if len(e.args) != 1:
                        raise SemaError("dict.contains() takes 1 argument", e.pos)
                    if A.expr_type(e.args[0]) != "str":
                        raise SemaError("dict.contains() key must be a str", e.pos)
                    e.inferred_type = "int"
                else:
                    raise SemaError(f"dict has no method {e.method!r}", e.pos)
            elif obj_t == "str":
                self._check_str_method(e)
                return
            elif obj_t.startswith("instance:"):
                class_name = obj_t.split(":", 1)[1]
                resolved = self._resolve_method(class_name, e.method)
                if resolved is None:
                    raise SemaError(
                        f"{class_name} has no method {e.method!r}",
                        e.pos,
                    )
                _, sig = resolved
                # Method arity counts self; user passed args don't include self.
                expected = sig.arity - 1
                required = expected - sig.n_defaults
                if not (required <= len(e.args) <= expected):
                    raise SemaError(
                        f"{class_name}.{e.method}() takes {required}..{expected} argument(s), got {len(e.args)}",
                        e.pos,
                    )
                # Methods always return int in current model.
                e.inferred_type = "int"
            else:
                raise SemaError(f"{obj_t} has no method {e.method!r}", e.pos)
            return
        raise SemaError(
            f"internal: unhandled expr {type(e).__name__}", getattr(e, "pos", None)
        )

    # Signature: (arg-types, return-type). The arg-types tuple may be empty.
    STR_METHODS = {
        "upper": ((), "str"),
        "lower": ((), "str"),
        "strip": ((), "str"),
        "lstrip": ((), "str"),
        "rstrip": ((), "str"),
        "startswith": (("str",), "int"),
        "endswith": (("str",), "int"),
        "find": (("str",), "int"),
        "count": (("str",), "int"),
        "replace": (("str", "str"), "str"),
    }

    def _check_str_method(self, e) -> None:
        sig = self.STR_METHODS.get(e.method)
        if sig is None:
            raise SemaError(f"str has no method {e.method!r}", e.pos)
        arg_types, ret = sig
        if len(e.args) != len(arg_types):
            raise SemaError(
                f"str.{e.method}() takes {len(arg_types)} argument(s), got {len(e.args)}",
                e.pos,
            )
        for i, (a, want) in enumerate(zip(e.args, arg_types)):
            got = A.expr_type(a)
            if got != want:
                raise SemaError(
                    f"str.{e.method}() argument {i + 1}: expected {want}, got {got}",
                    e.pos,
                )
        e.inferred_type = ret

    def _check_ffi_call(
        self, fn: stdlib.Func, args: list, pos, scope: Scope, *, label: str
    ) -> None:
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
                f"{label}() argument {i + 1}: expected {want}, got {got}",
                pos,
            )

    def _check_call(self, e: A.Call, scope: Scope) -> None:
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
                "len": "int",
                "int": "int",
                "float": "float",
                "str": "str",
                "input": "str",
            }[e.func]
            # Argument-type sanity for builtins that care.
            if e.func == "len":
                t = A.expr_type(e.args[0])
                if t not in ("str", "list", "dict"):
                    raise SemaError("len() requires a string, list, or dict", e.pos)
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
            required = sig.arity - sig.n_defaults
            if not (required <= len(e.args) <= sig.arity):
                if required == sig.arity:
                    raise SemaError(
                        f"{e.func}() takes {sig.arity} argument(s), got {len(e.args)}",
                        e.pos,
                    )
                raise SemaError(
                    f"{e.func}() takes {required}-{sig.arity} arguments, got {len(e.args)}",
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
        if e.func in self.classes:
            # Constructor call: ClassName(args). If __init__ exists, validate
            # arity against it (skipping `self`). Otherwise no args allowed.
            init = self._resolve_method(e.func, "__init__")
            if init is None:
                if e.args:
                    raise SemaError(
                        f"{e.func}() has no __init__ and takes no arguments",
                        e.pos,
                    )
            else:
                _, sig = init
                expected = sig.arity - 1
                required = expected - sig.n_defaults
                if not (required <= len(e.args) <= expected):
                    if required == expected:
                        raise SemaError(
                            f"{e.func}() takes {expected} argument(s), got {len(e.args)}",
                            e.pos,
                        )
                    raise SemaError(
                        f"{e.func}() takes {required}-{expected} arguments, got {len(e.args)}",
                        e.pos,
                    )
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = f"instance:{e.func}"
            return
        raise SemaError(f"undefined function {e.func!r}", e.pos)


def analyze(mod: A.Module) -> None:
    SemaAnalyzer(mod).analyze()
