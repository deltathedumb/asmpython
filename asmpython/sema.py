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
    "list": (1, 1),  # list(iterable) -> shallow copy as a list
    "set": (0, 1),  # set() / set(iterable)
    "frozenset": (0, 1),  # frozenset() / frozenset(iterable)
    "sum": (1, 2),  # sum(iterable[, start])
    "min": (1, 64),  # min(iterable) or min(a, b, ...)
    "max": (1, 64),  # max(iterable) or max(a, b, ...)
    "abs": (1, 1),
    "sorted": (1, 1),  # sorted(iterable) (key/reverse via kwargs)
    "reversed": (1, 1),
    "any": (1, 1),
    "all": (1, 1),
    "ord": (1, 1),
    "chr": (1, 1),
    "repr": (1, 1),
}


# Builtin exception classes. serpent's exception runtime is string-message
# based, but the *front end* must accept idiomatic `raise ValueError(msg)` and
# bare `raise NotImplementedError`. These names resolve as class objects and,
# when called, yield an (external) instance.
BUILTIN_EXCEPTIONS: frozenset[str] = frozenset({
    "BaseException", "Exception", "SystemExit", "KeyboardInterrupt",
    "RuntimeError", "NotImplementedError", "ValueError", "TypeError",
    "NameError", "AttributeError", "KeyError", "IndexError", "LookupError",
    "StopIteration", "ArithmeticError", "ZeroDivisionError", "OverflowError",
    "AssertionError", "ImportError", "OSError", "IOError", "FileNotFoundError",
})


@dataclass
class FuncSig:
    name: str
    arity: int
    pos: A.SourcePos
    # Number of trailing parameters that have default values; required
    # arity is `arity - n_defaults`. Caller may omit up to n_defaults of them.
    n_defaults: int = 0
    # Resolved return type as (ty, el_type, value_type), or None if the
    # function has no usable return annotation (treated as int at call sites).
    ret_type: object = None
    # Parameter names and their default expressions (parallel to params,
    # including `self` for methods). Used to bind keyword arguments onto
    # positions at call sites.
    param_names: list = field(default_factory=list)
    param_defaults: list = field(default_factory=list)
    # Name of the `*args` parameter (the trailing list slot), or None.
    vararg: Optional[str] = None
    # Per-slot kinds when the body returns a tuple (`return a, b`), so a call
    # site can unpack `x, y = obj.m()`. None when it doesn't return a tuple.
    ret_tuple: object = None


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
    # Field name -> static type ("int"/"str"/"float"/"list"/"dict"/"tuple"/
    # "instance:<Class>"), inferred from `self.x = <value>` assignments and
    # `self.x: T` annotations. Drives the type of `obj.x` reads. Unknown fields
    # read as int (the dict's int-default).
    fields: dict[str, str] = field(default_factory=dict)
    # Companion element-kind info for collection fields, so `self.xs[i]` and
    # `for x in self.xs` recover the kind. `field_el_types` holds the list
    # element kind (or dict value kind); `field_tuple_types` the per-slot kinds.
    field_el_types: dict[str, str] = field(default_factory=dict)
    field_tuple_types: dict[str, list] = field(default_factory=dict)


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
    # For names typed "list", element type — "int" / "str" / "float" /
    # "instance:<ClassName>". Mixed-type lists still wait on a tagged-value
    # runtime; we currently support homogeneous lists of any of those four
    # element kinds.
    list_el_types: dict[str, str] = field(default_factory=dict)
    # For names typed "dict", value kind. Keys are always str in v1.
    dict_value_types: dict[str, str] = field(default_factory=dict)
    # For names typed "tuple", the per-slot element kinds.
    tuple_elem_types: dict[str, list[str]] = field(default_factory=dict)

    @property
    def names(self):
        # Back-compat for the membership checks elsewhere.
        return self.types.keys()

    def add(
        self,
        name: str,
        ty: str = "int",
        *,
        el_type: str | None = None,
        value_type: str | None = None,
        tuple_types: list[str] | None = None,
    ) -> None:
        self.types[name] = ty
        if ty == "list" and el_type is not None:
            self.list_el_types[name] = el_type
        if ty == "dict" and value_type is not None:
            self.dict_value_types[name] = value_type
        if ty == "tuple" and tuple_types is not None:
            self.tuple_elem_types[name] = tuple_types

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
        # Module-level names (imports + top-level assignments). Populated by
        # analyze() before function/method bodies are checked.
        self.global_scope: Scope = Scope()
        self.loop_depth = 0
        self.in_function: Optional[str] = None
        # Name of the class whose method body is currently being checked, so
        # `super()` can resolve against its base. None outside a method.
        self.current_class: Optional[str] = None
        # Imported FFI: bindings either bound under a module prefix or
        # lifted directly into the namespace via from-import.
        self.imported_modules: dict[str, dict] = {}
        self.ffi_funcs: dict[str, stdlib.Func] = {}
        self.ffi_consts: dict[str, stdlib.Const] = {}
        # name -> per-slot element kinds for functions that return a tuple
        # (i.e. have a `return a, b` somewhere). Lets `q, r = f()` recover
        # the per-target types at the call site. Computed in analyze().
        self.func_ret_tuple: dict[str, list[str]] = {}

    def _has_external_base(self, class_name: str) -> bool:
        """True if `class_name` or any ancestor inherits from a base that isn't
        a user-defined class (a builtin like Exception, or a name imported from
        another module). Such a base may supply methods/fields serpent can't
        see, so member access against it is checked leniently."""
        cur = class_name
        seen: set = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls = self.classes.get(cur)
            if cls is None:
                return False
            if cls.parent is not None and cls.parent not in self.classes:
                return True
            cur = cls.parent
        return False

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

    def _resolve_field_type(self, class_name: str, field_name: str) -> Optional[str]:
        """Walk the parent chain to find the static type of an instance field.
        Returns the type string or None if no class in the chain declares it."""
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls = self.classes.get(cur)
            if cls is None:
                return None
            if field_name in cls.fields:
                return cls.fields[field_name]
            cur = cls.parent
        return None

    def _resolve_field_el(self, class_name: str, field_name: str) -> str:
        """Element kind (list element / dict value) of a collection field,
        walking the parent chain. 'int' when unknown."""
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls = self.classes.get(cur)
            if cls is None:
                return "int"
            if field_name in cls.field_el_types:
                return cls.field_el_types[field_name]
            if field_name in cls.fields:
                return "int"  # declared here without an element kind
            cur = cls.parent
        return "int"

    def _resolve_field_tuple(self, class_name: str, field_name: str) -> list:
        """Per-slot kinds of a tuple field, walking the parent chain; [] if
        unknown."""
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls = self.classes.get(cur)
            if cls is None:
                return []
            if field_name in cls.field_tuple_types:
                return list(cls.field_tuple_types[field_name])
            if field_name in cls.fields:
                return []
            cur = cls.parent
        return []

    # ---- instance field type inference --------------------------------------

    def _collect_field_types(self) -> None:
        """Infer each class's instance-field types from `self.x = <value>`
        assignments in its methods. The assigned value's static type becomes
        the field's type, so `obj.x` reads recover str / instance / list fields
        instead of defaulting to int. Param types feed the inference (e.g.
        `def __init__(self, p: str): self.p = p` makes field `p` a str)."""
        for c in self.mod.classes:
            sig = self.classes[c.name]
            # Class-body variables become fields too (`self.NAME` reads them).
            # Type from the annotation when present, else the initializer's
            # static type.
            for cv in getattr(c, "class_vars", []):
                cname, cannot, cvalue = cv
                r = self._resolve_annot(cannot)
                if r is not None:
                    ty, el, val, _tup = r
                elif cvalue is not None:
                    ty, el, val = A.expr_type(cvalue), None, None
                else:
                    ty, el, val = "int", None, None
                sig.fields[cname] = ty
                if ty == "list" and el is not None:
                    sig.field_el_types[cname] = el
                elif ty == "dict" and val is not None:
                    sig.field_el_types[cname] = val
            for m in c.methods:
                # Each param maps to its resolved annotation tuple
                # (ty, el, val, tuple) so a `self.x = param` assignment can carry
                # the param's element/value kinds onto the field.
                pinfo: dict = {}
                for i, p in enumerate(m.params):
                    if i == 0:
                        continue  # self
                    annot = m.param_types[i] if i < len(m.param_types) else None
                    r = self._resolve_annot(annot)
                    if r is not None:
                        pinfo[p] = r
                    elif i < len(m.defaults) and m.defaults[i] is not None:
                        pinfo[p] = (A.expr_type(m.defaults[i]), None, None, None)
                self._scan_field_assigns(m.body, sig, pinfo)

    def _scan_field_assigns(self, stmts: list, sig: ClassSig, pinfo: dict) -> None:
        for s in stmts:
            if (
                isinstance(s, A.AttrAssign)
                and isinstance(s.obj, A.Name)
                and s.obj.name == "self"
            ):
                # An explicit declaration annotation (`self.x: T = ...`) wins —
                # it carries element/value kinds the initializer (often `{}`/`[]`)
                # can't. Otherwise fall back to the value's static type.
                r = self._resolve_annot(getattr(s, "annot", None))
                if r is not None:
                    ty, el, val, tup = r
                else:
                    ty, el, val, tup = self._static_value_info(s.value, pinfo)
                existing = sig.fields.get(s.name)
                # Don't let a later `= 0` reset placeholder downgrade a field we
                # already typed more precisely.
                if existing is None or (existing == "int" and ty != "int"):
                    sig.fields[s.name] = ty
                    if ty == "list" and el is not None:
                        sig.field_el_types[s.name] = el
                    elif ty == "dict" and val is not None:
                        sig.field_el_types[s.name] = val
                    elif ty == "tuple" and tup:
                        sig.field_tuple_types[s.name] = tup
            elif isinstance(s, A.If):
                self._scan_field_assigns(s.then, sig, pinfo)
                self._scan_field_assigns(s.orelse, sig, pinfo)
            elif isinstance(s, A.While):
                self._scan_field_assigns(s.body, sig, pinfo)
            elif isinstance(s, A.For):
                self._scan_field_assigns(s.body, sig, pinfo)
            elif isinstance(s, A.Try):
                self._scan_field_assigns(s.body, sig, pinfo)
                self._scan_field_assigns(s.handler, sig, pinfo)
                for _bind, hbody in s.extra_handlers:
                    self._scan_field_assigns(hbody, sig, pinfo)
                self._scan_field_assigns(s.else_body, sig, pinfo)
                self._scan_field_assigns(s.finally_body, sig, pinfo)

    def _static_value_info(self, value, pinfo: dict):
        """Best-effort (ty, el, val, tuple) of an assigned value, used for field
        inference before full body analysis (so it can't rely on stamped
        inferred_type). Covers the dataclass-style cases that matter. `el`/`val`
        are the list-element / dict-value kinds; `tuple` the per-slot kinds."""
        if isinstance(value, A.IntLit):
            return ("int", None, None, None)
        if isinstance(value, A.FloatLit):
            return ("float", None, None, None)
        if isinstance(value, (A.StrLit, A.FString)):
            return ("str", None, None, None)
        if isinstance(value, A.ListLit):
            return ("list", None, None, None)
        if isinstance(value, A.DictLit):
            return ("dict", None, None, None)
        if isinstance(value, A.TupleLit):
            return ("tuple", None, None, None)
        if isinstance(value, A.Name):
            return pinfo.get(value.name, ("int", None, None, None))
        if isinstance(value, A.Call) and value.func in self.classes:
            return (f"instance:{value.func}", None, None, None)
        return ("int", None, None, None)

    def _static_value_type(self, value, ptypes: dict) -> str:
        """Just the type half of `_static_value_info` (kept for callers that
        only need the field's base type)."""
        ty, _el, _val, _tup = self._static_value_info(value, ptypes)
        return ty

    # ---- parameter annotation resolution ------------------------------------

    def _resolve_scalar_annot(self, base) -> str:
        """An element/value base from an annotation -> a serpent scalar type."""
        if base is None:
            # A bare `list` / `dict` with no element annotation: the element
            # kind is unknown, so stay opaque ("any") rather than guessing int.
            # That keeps `xs.append(<anything>)` and element reads lenient.
            return "any"
        if base in ("int", "str", "float"):
            return base
        if base in ("list", "dict", "tuple"):
            # A nested collection element/value (`dict[str, list[str]]`): every
            # value is an 8-byte pointer, so the container kind passes through.
            return base
        if base == "set":
            return "set"
        if base in self.classes:
            return f"instance:{base}"
        # A capitalized external/imported class (`list[Token]`, `dict[str, Expr]`):
        # model the element as an opaque instance so attribute/method access on
        # elements read out of the container stays lenient (mirrors
        # _resolve_annot's handling of a bare external annotation).
        leaf = base.split(".")[-1] if isinstance(base, str) else ""
        if leaf[:1].isupper():
            return f"instance:{leaf}"
        return "int"

    def _resolve_annot(self, annot: tuple):
        """Turn a parser annotation descriptor (base, el) into
        (ty, el_type, value_type, tuple_types), or None if it doesn't
        constrain the type (so the caller falls back to default inference)."""
        if annot is None:
            return None
        base, el = annot
        if base in ("int", "str", "float"):
            return (base, None, None, None)
        if base == "list":
            return ("list", self._resolve_scalar_annot(el), None, None)
        if base == "dict":
            return ("dict", None, self._resolve_scalar_annot(el), None)
        if base == "tuple":
            # Annotations don't give per-slot kinds; leave them unknown.
            return ("tuple", None, None, [])
        if base == "any":
            # An explicit opaque annotation (`object`, `Any`, or a genuine
            # multi-type union the parser collapsed to "any"): constrain the
            # value to the lenient "any" type rather than leaving it to default
            # to int. Lets a `-> str | list` method type its result usefully.
            return ("any", None, None, None)
        if base in ("none", "set"):
            return None
        if base in self.classes:
            return (f"instance:{base}", None, None, None)
        # An external / imported class annotation (`Token`, `A.IntLit`,
        # `FuncInfo`). We can't see its methods or fields, so model it as an
        # opaque instance: attribute and method access against it are checked
        # leniently (see _check_expr's Attr / MethodCall handling). The leaf of
        # a dotted path is the class-ish name.
        leaf = base.split(".")[-1]
        if leaf[:1].isupper():
            return (f"instance:{leaf}", None, None, None)
        # A lowercase unknown name (a type alias we don't model) — don't
        # constrain; the body's usage decides what's legal.
        return None

    def _seed_param(self, scope: Scope, name: str, annot, default_expr) -> None:
        """Add a parameter to `scope`, typing it from its annotation if present,
        otherwise from a literal default, otherwise int."""
        resolved = self._resolve_annot(annot)
        if resolved is not None:
            ty, el, val, tup = resolved
            scope.add(name, ty, el_type=el, value_type=val, tuple_types=tup)
            return
        ty = "int"
        if default_expr is not None:
            ty = A.expr_type(default_expr)
        scope.add(name, ty)

    def _seed_globals_into(self, scope: Scope) -> None:
        """Copy module-level names (and their tracked types) into a fresh
        function/method scope so global reads resolve. Locals declared later in
        the body simply overwrite these entries, giving locals precedence."""
        g = self.global_scope
        scope.types.update(g.types)
        scope.list_el_types.update(g.list_el_types)
        scope.dict_value_types.update(g.dict_value_types)
        scope.tuple_elem_types.update(g.tuple_elem_types)

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
            r = self._resolve_annot(f.ret_type)
            self.funcs[f.name] = FuncSig(
                name=f.name,
                arity=len(f.params),
                n_defaults=sum(1 for d in f.defaults if d is not None),
                pos=f.pos,
                ret_type=(r[0], r[1], r[2]) if r is not None else None,
                param_names=list(f.params),
                param_defaults=list(f.defaults),
                vararg=f.vararg,
            )

        # Infer which functions return a tuple, and the shape of that tuple,
        # so call sites can unpack `q, r = f()`. Done before body analysis so
        # forward references and recursion still see the inferred shape.
        for f in self.mod.funcs:
            ets = self._scan_tuple_return(f.body)
            if ets is not None:
                self.func_ret_tuple[f.name] = ets

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
                mr = self._resolve_annot(m.ret_type)
                sig.methods[m.name] = FuncSig(
                    name=m.name,
                    arity=len(m.params),
                    n_defaults=sum(1 for d in m.defaults if d is not None),
                    pos=m.pos,
                    ret_type=(mr[0], mr[1], mr[2]) if mr is not None else None,
                    param_names=list(m.params),
                    param_defaults=list(m.defaults),
                    vararg=m.vararg,
                    ret_tuple=self._scan_tuple_return(m.body),
                )
            self.classes[c.name] = sig

        # Validate parents and check for cycles. A parent that isn't a
        # user-defined class is treated as an *external* base — a builtin
        # (e.g. `Exception`) or a name imported from another module
        # (e.g. `Codegen` via `from .codegen import Codegen`). serpent doesn't
        # model an external base's methods or fields, so it contributes no
        # inherited members; method resolution simply stops at it.
        for c in self.mod.classes:
            if c.parent is not None and c.parent in self.classes:
                # Cycle check only walks the user-class chain.
                seen, cur = {c.name}, c.parent
                while cur is not None and cur in self.classes:
                    if cur in seen:
                        raise SemaError(
                            f"inheritance cycle involving {c.name!r}", c.pos
                        )
                    seen.add(cur)
                    cur = self.classes[cur].parent

        # Top-level body first, so module-level names (imports and top-level
        # assignments) are recorded as globals and become visible inside
        # function/method bodies. In CPython a function reads module globals at
        # call time; we approximate that by seeding each body's scope with the
        # names the module level defines.
        # Infer instance-field types from `self.x = ...` so `obj.x` reads carry
        # the right static type. Done before any body is checked (top-level or
        # method) so every field read — including from module-level code — sees
        # the inferred field types.
        self._collect_field_types()

        self.global_scope = Scope()
        # Module dunders the runtime always provides.
        self.global_scope.add("__name__", "str")
        self._check_block(self.mod.body, self.global_scope)

        # Function bodies: each has its own scope, seeded with globals then
        # params. If a param has a default literal, infer its type from the
        # default (so `def greet(p="hi")` makes p a str in the body).
        for f in self.mod.funcs:
            self.in_function = f.name
            scope = Scope()
            self._seed_globals_into(scope)
            for i, p in enumerate(f.params):
                annot = f.param_types[i] if i < len(f.param_types) else None
                default = f.defaults[i] if i < len(f.defaults) else None
                self._seed_param(scope, p, annot, default)
            self._check_block(f.body, scope)
            self.in_function = None

        # Method bodies: `self` is typed as the instance of its class.
        for c in self.mod.classes:
            for m in c.methods:
                self.in_function = f"{c.name}__{m.name}"
                self.current_class = c.name
                scope = Scope()
                self._seed_globals_into(scope)
                scope.add("self", f"instance:{c.name}")
                for i, p in enumerate(m.params[1:], start=1):
                    annot = m.param_types[i] if i < len(m.param_types) else None
                    default = m.defaults[i] if i < len(m.defaults) else None
                    self._seed_param(scope, p, annot, default)
                self._check_block(m.body, scope)
                self.in_function = None
                self.current_class = None

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

    def _narrow_type_of(self, te) -> str:
        """The type a variable narrows to inside an `isinstance(x, te)` guard.
        `te` is the second argument: a class reference (`A.Call`, `Token`), a
        builtin type name, or a tuple of them (which can't pick one -> 'any')."""
        if isinstance(te, A.Name):
            nm = te.name
            if nm == "bool":
                return "int"
            if nm in ("int", "str", "float", "list", "dict", "tuple", "set"):
                return nm
            if nm in self.classes:
                return f"instance:{nm}"
            if nm[:1].isupper():
                return f"instance:{nm}"
            return "any"
        if isinstance(te, A.Attr):
            leaf = te.name
            if leaf[:1].isupper():
                return f"instance:{leaf}"
            return "any"
        return "any"

    def _isinstance_narrow_spec(self, expr):
        """(name, narrowed-type) when `expr` is `isinstance(NAME, TYPE)`, else
        None. Only a bare-name first argument is narrowable."""
        if (
            isinstance(expr, A.Call)
            and expr.func == "isinstance"
            and len(expr.args) == 2
            and isinstance(expr.args[0], A.Name)
        ):
            return (expr.args[0].name, self._narrow_type_of(expr.args[1]))
        return None

    def _test_narrow_spec(self, test):
        """The narrowing implied by a boolean condition: a bare
        `isinstance(x, T)` or the leading conjunct of an `and` chain."""
        spec = self._isinstance_narrow_spec(test)
        if spec is not None:
            return spec
        if isinstance(test, A.BoolOp) and test.op == "and":
            return self._test_narrow_spec(test.left)
        return None

    def _apply_narrow(self, spec, scope: Scope):
        """Narrow `spec`'s (name, type) in `scope`; return a restore token
        (name, had_before, saved_type) to pass to `_undo_narrow`."""
        name, nty = spec
        token = (name, name in scope.types, scope.types.get(name))
        scope.types[name] = nty
        return token

    def _undo_narrow(self, token, nty, scope: Scope) -> None:
        """Restore a narrowed name unless the branch reassigned it (Python lets
        in-branch assignments leak out, so we only undo our own override)."""
        name, had, saved = token
        if scope.types.get(name) == nty:  # not reassigned inside the branch
            if had:
                scope.types[name] = saved
            else:
                scope.types.pop(name, None)

    def _flat_target_names(self, targets: list) -> list:
        """Flatten a for-loop target list (entries may be names or nested name
        groups) into the bare names it binds."""
        out: list = []
        for t in targets:
            if isinstance(t, list):
                out.extend(t)
            else:
                out.append(t)
        return out

    def _for_zip_spec(self, s: A.For):
        """Recognize the parallel-iteration loop shapes
        `for a, b in zip(A, B)` and `for i, (a, b) in enumerate(zip(A, B))`.

        Returns (idx_name_or_None, a_name, b_name, a_expr, b_expr) when `s`
        matches, otherwise None (so the caller falls back to ordinary handling).
        """
        it = s.iter
        if it is None or not isinstance(it, A.Call):
            return None
        if it.func == "zip":
            if (
                len(it.args) == 2
                and len(s.targets) == 2
                and isinstance(s.targets[0], str)
                and isinstance(s.targets[1], str)
            ):
                return (None, s.targets[0], s.targets[1], it.args[0], it.args[1])
            return None
        if (
            it.func == "enumerate"
            and len(it.args) == 1
            and isinstance(it.args[0], A.Call)
            and it.args[0].func == "zip"
        ):
            z = it.args[0]
            if (
                len(z.args) == 2
                and len(s.targets) == 2
                and isinstance(s.targets[0], str)
                and isinstance(s.targets[1], list)
                and len(s.targets[1]) == 2
            ):
                return (
                    s.targets[0],
                    s.targets[1][0],
                    s.targets[1][1],
                    z.args[0],
                    z.args[1],
                )
            return None
        return None

    def _iter_element_type(self, e, scope: Scope) -> str:
        """Element type yielded by iterating `e` (a list/str/dict/tuple/any)."""
        t = A.expr_type(e)
        if t == "list":
            return self._list_el_type(e, scope)
        if t in ("str", "dict"):
            return "str"
        if t == "tuple":
            ets = self._tuple_elem_types(e, scope)
            return ets[0] if ets and all(x == ets[0] for x in ets) else "int"
        if t == "any":
            return "any"
        return "int"

    def _list_el_type(self, e, scope: Scope) -> str:
        """Element type of a list-valued expression. 'int' if unknown."""
        if isinstance(e, A.ListLit):
            return e.el_type
        if isinstance(e, A.Comprehension):
            return e.list_el_type
        if isinstance(e, A.Name):
            return scope.list_el_types.get(e.name, "int")
        if isinstance(e, A.MethodCall):
            # `dict.keys()` returns list[str]; `dict.values()` returns list[int].
            return getattr(e, "list_el_type", "int")
        if isinstance(e, A.Call):
            # A user function annotated `-> list[T]` stamps T on the call node.
            return getattr(e, "list_el_type", "int")
        if isinstance(e, A.Subscript):
            # List slicing preserves element kind; sema stamps it onto the
            # Subscript node.
            return getattr(e, "list_el_type", "int")
        if isinstance(e, A.Attr):
            # An instance field typed list[T]: sema stamped T onto the Attr.
            return getattr(e, "list_el_type", "int")
        if isinstance(e, A.IfExp):
            # A conditional whose arms are lists: sema stamped the element kind.
            return getattr(e, "list_el_type", "int")
        return "int"

    def _dict_value_type(self, e, scope: Scope) -> str:
        """Value type of a dict-valued expression. 'int' if unknown."""
        if isinstance(e, A.DictLit):
            return getattr(e, "value_type", "int")
        if isinstance(e, A.Name):
            return scope.dict_value_types.get(e.name, "int")
        if isinstance(e, A.Attr):
            return getattr(e, "value_type", "int")
        return "int"

    def _tuple_elem_types(self, e, scope: Scope) -> list[str]:
        """Per-slot element kinds of a tuple-valued expression, or [] if
        unknown. Mirrors `_list_el_type` but yields the whole heterogeneous
        list rather than a single element kind."""
        if isinstance(e, A.TupleLit):
            return list(e.elem_types)
        if isinstance(e, A.Name):
            return list(scope.tuple_elem_types.get(e.name, []))
        if isinstance(e, (A.Call, A.Subscript, A.Attr, A.MethodCall)):
            return list(getattr(e, "tuple_elem_types", []))
        return []

    def _scan_tuple_return(self, stmts: list) -> Optional[list[str]]:
        """Infer the per-slot kinds of a function's tuple return (`return a, b`),
        or None if it never returns a tuple.

        All `return <tuple>` sites of the dominant arity are merged: a slot that
        every return agrees on keeps that kind, a slot they disagree on becomes
        "any". This keeps unpack arity stable while not over-committing the slot
        type for functions with heterogeneous returns (e.g. `_resolve_annot`,
        whose slots are sometimes a name and sometimes None)."""
        shapes: list = []
        self._collect_tuple_returns(stmts, shapes)
        if not shapes:
            return None
        arity = len(shapes[0])
        same = [sh for sh in shapes if len(sh) == arity]
        merged: list = []
        for i in range(arity):
            kinds = set(sh[i] for sh in same)
            merged.append(kinds.pop() if len(kinds) == 1 else "any")
        return merged

    def _collect_tuple_returns(self, stmts: list, acc: list) -> None:
        for s in stmts:
            if isinstance(s, A.Return) and isinstance(s.value, A.TupleLit):
                acc.append([A.expr_type(el) for el in s.value.elems])
            elif isinstance(s, A.If):
                self._collect_tuple_returns(s.then, acc)
                self._collect_tuple_returns(s.orelse, acc)
            elif isinstance(s, (A.While, A.For)):
                self._collect_tuple_returns(s.body, acc)
            elif isinstance(s, A.Try):
                self._collect_tuple_returns(s.body, acc)
                self._collect_tuple_returns(s.handler, acc)

    def _check_stmt(self, s, scope: Scope) -> None:
        if isinstance(s, A.Pass):
            return
        if isinstance(s, A.Assign):
            self._check_expr(s.value, scope)
            t = A.expr_type(s.value)
            if t == "list":
                scope.add(s.target, t, el_type=self._list_el_type(s.value, scope))
            elif t == "dict":
                scope.add(s.target, t, value_type=self._dict_value_type(s.value, scope))
            elif t == "tuple":
                scope.add(s.target, t, tuple_types=self._tuple_elem_types(s.value, scope))
            else:
                scope.add(s.target, t)
            return
        if isinstance(s, A.TupleAssign):
            # Resolve the RHS first so tuple-returning calls have their type
            # set before we decide between unpack and parallel forms.
            for v in s.values:
                self._check_expr(v, scope)
            # Unpack form: `a, b = <single tuple expr>` (a literal, a tuple
            # variable, or a call to a tuple-returning function).
            if len(s.values) == 1 and A.expr_type(s.values[0]) == "tuple":
                ets = self._tuple_elem_types(s.values[0], scope)
                if ets and len(ets) != len(s.targets):
                    raise SemaError(
                        f"cannot unpack {len(ets)}-tuple into {len(s.targets)} target(s)",
                        s.pos,
                    )
                # Unknown per-slot kinds (an opaque/unannotated tuple) -> bind
                # each target opaque so its later use stays lenient.
                for i, t in enumerate(s.targets):
                    scope.add(t, ets[i] if i < len(ets) else "any")
                return
            if len(s.values) == 1 and A.expr_type(s.values[0]) == "any":
                # Unpacking an opaque value (e.g. a tuple read out of an
                # unannotated container): bind every target leniently.
                for t in s.targets:
                    scope.add(t, "any")
                return
            # Parallel form: `a, b = e1, e2`.
            if len(s.targets) != len(s.values):
                raise SemaError(
                    f"tuple assign expects {len(s.targets)} values, got {len(s.values)}",
                    s.pos,
                )
            for t, v in zip(s.targets, s.values):
                vt = A.expr_type(v)
                # Parallel assignment moves each value through rax, so any
                # 8-byte scalar works (int / str-ptr / instance-ptr). Floats
                # live in xmm and aren't plumbed through this path yet.
                if vt == "float":
                    raise SemaError(
                        f"tuple assign target {t!r}: float values aren't supported in "
                        "parallel assignment yet (assign separately)",
                        s.pos,
                    )
                if (
                    vt not in ("int", "str", "any", "list", "dict", "tuple", "set")
                    and not vt.startswith("instance:")
                ):
                    raise SemaError(
                        f"tuple assign target {t!r}: unsupported value type {vt}",
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
            # An `if isinstance(x, T):` guard narrows x inside the then-block so
            # `x.attr` reads resolve (the dispatch pattern throughout serpent).
            spec = self._test_narrow_spec(s.test)
            if spec is not None:
                token = self._apply_narrow(spec, scope)
                self._check_block(s.then, scope)
                self._undo_narrow(token, spec[1], scope)
            else:
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
            # zip(A, B) / enumerate(zip(A, B)): parallel iteration with an
            # optional index. Recognized before the plain-enumerate handler.
            zspec = self._for_zip_spec(s)
            if zspec is not None:
                idx_name, a_name, b_name, a_expr, b_expr = zspec
                self._check_expr(a_expr, scope)
                self._check_expr(b_expr, scope)
                if A.expr_type(a_expr) not in ("list", "any") or A.expr_type(
                    b_expr
                ) not in ("list", "any"):
                    raise SemaError("zip() arguments must be lists", s.pos)
                if idx_name is not None:
                    scope.add(idx_name, "int")
                scope.add(a_name, self._iter_element_type(a_expr, scope))
                scope.add(b_name, self._iter_element_type(b_expr, scope))
                self.loop_depth += 1
                try:
                    self._check_block(s.body, scope)
                finally:
                    self.loop_depth -= 1
                return
            # enumerate(iterable): `for i, x in enumerate(xs)` binds the index
            # and element. Intercepted before the generic call check (enumerate
            # is only meaningful in this loop position).
            if (
                s.iter is not None
                and isinstance(s.iter, A.Call)
                and s.iter.func == "enumerate"
            ):
                if len(s.iter.args) != 1:
                    raise SemaError("enumerate() takes 1 argument", s.pos)
                if len(s.targets) != 2:
                    raise SemaError(
                        "for ... in enumerate(...) needs two targets "
                        "(`for i, x in enumerate(xs)`)",
                        s.pos,
                    )
                inner = s.iter.args[0]
                self._check_expr(inner, scope)
                scope.add(s.targets[0], "int")
                scope.add(s.targets[1], self._iter_element_type(inner, scope))
                self.loop_depth += 1
                try:
                    self._check_block(s.body, scope)
                finally:
                    self.loop_depth -= 1
                return
            if s.iter is not None:
                self._check_expr(s.iter, scope)
                it_t = A.expr_type(s.iter)
                if it_t == "list":
                    scope.add(s.var, self._list_el_type(s.iter, scope))
                elif it_t == "tuple":
                    # Iterating a tuple needs a single element type, so only
                    # homogeneous tuples may be iterated; index heterogeneous
                    # ones instead.
                    ets = self._tuple_elem_types(s.iter, scope)
                    if not ets:
                        scope.add(s.var, "int")
                    elif all(e == ets[0] for e in ets):
                        scope.add(s.var, ets[0])
                    else:
                        raise SemaError(
                            "cannot iterate a heterogeneous tuple; index its elements instead",
                            s.pos,
                        )
                elif it_t == "dict":
                    # Iterating a dict yields its keys (strings).
                    scope.add(s.var, "str")
                elif it_t == "str":
                    # Each iteration yields a fresh 1-char str.
                    scope.add(s.var, "str")
                elif it_t == "any":
                    # Opaque iterable (e.g. a value read out of an unannotated
                    # container): bind every target — including the multi-target
                    # `for a, b in <any>` forms — leniently.
                    if s.targets:
                        for nm in self._flat_target_names(s.targets):
                            scope.add(nm, "any")
                    else:
                        scope.add(s.var, "any")
                else:
                    raise SemaError(
                        "serpent 'for' iterates over range(), list, dict, tuple, or str",
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
                # `from . import ast_nodes as A` (no module name, just dots)
                # imports sibling *modules* — bind them as modules so
                # `A.Module(...)` / `A.expr_type(...)` stay lenient instead of
                # erroring as method calls on an int.
                bind_ty = "module" if not s.module else "int"
                for name in s.names:
                    scope.add(name, bind_ty)
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
                # "int" doubles as serpent's unknown/default kind, so treat it
                # (like "?"/"any") as a wildcard rather than a hard mismatch —
                # serpent's shallow inference types many str/instance values as
                # int. Same rule as append.
                if (
                    el_t not in ("?", "any", "int")
                    and value_t not in ("any", "int")
                    and value_t != el_t
                ):
                    raise SemaError(
                        f"list[i] = v: list element type is {el_t}, got {value_t}",
                        s.pos,
                    )
            elif obj_t == "dict":
                if A.expr_type(s.target.index) not in ("str", "any"):
                    raise SemaError("dict keys must be strings", s.pos)
                dvt = self._dict_value_type(s.target.obj, scope)
                if (
                    dvt not in ("any", "int")
                    and value_t not in ("any", "int")
                    and value_t != dvt
                ):
                    raise SemaError(
                        f"dict[k] = v: dict values are {dvt}, got {value_t}",
                        s.pos,
                    )
            elif obj_t == "any":
                pass  # opaque target: accept the index assignment leniently
            else:
                raise SemaError(f"cannot index a {obj_t}", s.pos)
            return
        if isinstance(s, A.AttrAssign):
            self._check_expr(s.obj, scope)
            obj_t = A.expr_type(s.obj)
            if not obj_t.startswith("instance:") and obj_t != "any":
                raise SemaError(
                    f"cannot assign attribute on {obj_t}",
                    s.pos,
                )
            self._check_expr(s.value, scope)
            value_t = A.expr_type(s.value)
            # Instance fields hold any 8-byte value (int / str-ptr / instance /
            # list / dict / tuple). Floats are the exception: they'd need xmm
            # spilling into the dict slot, which the field codegen doesn't do.
            user_instance = obj_t.startswith("instance:") and (
                obj_t.split(":", 1)[1] in self.classes
            )
            if user_instance and value_t == "float":
                raise SemaError(
                    "float instance attributes are not supported yet",
                    s.pos,
                )
            # Keep the class's field table in sync with assignments made after
            # the inference pass (e.g. a field first assigned in a later method).
            if user_instance and isinstance(s.obj, A.Name) and s.obj.name == "self":
                cls = obj_t.split(":", 1)[1]
                sig = self.classes[cls]
                if s.name not in sig.fields or (
                    sig.fields[s.name] == "int" and value_t not in ("int", "any")
                ):
                    sig.fields[s.name] = value_t
            return
        if isinstance(s, A.Try):
            self._check_block(s.body, scope)
            if s.bind_name is not None:
                scope.add(s.bind_name, "str")
            self._check_block(s.handler, scope)
            for bind_name, hbody in s.extra_handlers:
                if bind_name is not None:
                    scope.add(bind_name, "str")
                self._check_block(hbody, scope)
            self._check_block(s.else_body, scope)
            self._check_block(s.finally_body, scope)
            return
        if isinstance(s, A.Raise):
            self._check_expr(s.value, scope)
            vt = A.expr_type(s.value)
            # Accept a bare string message (serpent's native exception payload),
            # an exception object / bare exception class, or a constructor call
            # like `raise SemaError(msg, pos)` / `raise ValueError(...)`. The
            # constructor's class is often imported (so it reads as `int` here),
            # so we recognise it structurally: a Call to a Capitalized name.
            is_exc_ctor = isinstance(s.value, A.Call) and (
                s.value.func in BUILTIN_EXCEPTIONS or s.value.func[:1].isupper()
            )
            if (
                vt != "str"
                and not vt.startswith("instance:")
                and vt != "type"
                and not is_exc_ctor
            ):
                raise SemaError(
                    "raise requires a string message or an exception", s.pos
                )
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
            # A class name used as a value (passed to isinstance, stored, etc.)
            # is a first-class "type" object. Builtin exception classes count.
            if e.name in self.classes or e.name in BUILTIN_EXCEPTIONS:
                e.inferred_type = "type"
                return
            if e.name not in scope:
                raise SemaError(f"undefined variable {e.name!r}", e.pos)
            e.inferred_type = scope.types[e.name]
            if e.inferred_type == "list":
                e.list_el_type = scope.list_el_types.get(e.name, "int")
            elif e.inferred_type == "tuple":
                e.tuple_elem_types = list(scope.tuple_elem_types.get(e.name, []))
            return
        if isinstance(e, A.UnaryOp):
            self._check_expr(e.operand, scope)
            return
        if isinstance(e, A.BinOp):
            self._check_expr(e.left, scope)
            self._check_expr(e.right, scope)
            lt, rt = A.expr_type(e.left), A.expr_type(e.right)
            # An opaque ("any") operand short-circuits type checking: we can't
            # know its real type, so the result is opaque too — except that a
            # `+` with a str operand is unambiguously concatenation, so the str
            # pins the result type (otherwise the concatenated value would print
            # / chain as an int).
            if "any" in (lt, rt):
                if e.op == "+" and "str" in (lt, rt):
                    e.inferred_type = "str"
                    return
                e.inferred_type = "any"
                return
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
            # A union of class objects (`Stmt = Assign | AugAssign | ...`) is a
            # type-alias expression: `type | type` collapses to `type`.
            if e.op == "|" and lt == "type" and rt == "type":
                e.inferred_type = "type"
                return
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
                if "any" in (lt, rt):
                    # An opaque operand: compare at the raw 8-byte level and
                    # don't type-check the pairing.
                    continue
                if op in ("in", "not in"):
                    # Supported forms today:
                    #   str  in str
                    #   T    in list[T]          (T = int | str | float)
                    #   str  in dict             (dicts are str-keyed)
                    if lt == "str" and rt == "str":
                        continue
                    if rt == "list":
                        el_t = self._list_el_type(e.operands[i + 1], scope)
                        if lt != el_t:
                            raise SemaError(
                                f"'{op}': needle is {lt} but list elements are {el_t}",
                                e.pos,
                            )
                        continue
                    if rt == "dict":
                        if lt not in ("str", "any", "int"):
                            raise SemaError(
                                f"'{op}' on dict requires str key, got {lt}",
                                e.pos,
                            )
                        continue
                    if rt == "tuple":
                        # `x in (a, b, ...)` — tuples reuse the list layout, so a
                        # homogeneous tuple is scanned exactly like a list. Mixed
                        # element kinds would need per-slot comparison; reject
                        # those (serpent's own membership tests are homogeneous).
                        ets = A.tuple_element_types(e.operands[i + 1])
                        kinds = set(t for t in ets if t != "any")
                        if len(kinds) > 1:
                            raise SemaError(
                                "'in' on a heterogeneous tuple is unsupported",
                                e.pos,
                            )
                        # "int" doubles as the unknown sentinel, so it's a lenient
                        # needle (serpent's shallow inference types many strings
                        # as int).
                        if kinds and lt not in ("any", "int") and lt not in kinds:
                            only = next(iter(kinds))
                            raise SemaError(
                                f"'{op}': needle is {lt} but tuple elements are {only}",
                                e.pos,
                            )
                        continue
                    if rt == "set":
                        # `x in {…}`: sets only model membership; the element
                        # kind isn't tracked, so accept any needle.
                        continue
                    raise SemaError(
                        f"'{op}' not supported between {lt} and {rt}",
                        e.pos,
                    )
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
                        # Equality against the unknown "int" sentinel is allowed
                        # (a str value shallow-inferred as int compared to a str
                        # literal, common in serpent's own source). Ordering and
                        # other concrete mismatches stay strict.
                        if op in ("==", "!=") and "int" in (lt, rt):
                            continue
                        raise SemaError(
                            f"cannot compare {lt} and {rt} with {op!r}",
                            e.pos,
                        )
            return
        if isinstance(e, A.BoolOp):
            self._check_expr(e.left, scope)
            # `isinstance(x, T) and <expr using x.attr>`: narrow x while the
            # right operand is checked (flow typing within the conjunction).
            spec = self._test_narrow_spec(e.left) if e.op == "and" else None
            if spec is not None:
                token = self._apply_narrow(spec, scope)
                self._check_expr(e.right, scope)
                self._undo_narrow(token, spec[1], scope)
            else:
                self._check_expr(e.right, scope)
            return
        if isinstance(e, A.IfExp):
            self._check_expr(e.test, scope)
            # `x.attr if isinstance(x, T) else ...`: narrow x in the body arm.
            spec = self._test_narrow_spec(e.test)
            if spec is not None:
                token = self._apply_narrow(spec, scope)
                self._check_expr(e.body, scope)
                self._undo_narrow(token, spec[1], scope)
            else:
                self._check_expr(e.body, scope)
            self._check_expr(e.orelse, scope)
            bt = A.expr_type(e.body)
            ot = A.expr_type(e.orelse)
            if "any" in (bt, ot):
                # An opaque arm makes the whole expression opaque — we can't
                # know its type, so stay lenient rather than rejecting the
                # mismatch (covers `x[0] if x else None`-style guards).
                e.inferred_type = "any"
            elif bt == ot:
                e.inferred_type = bt
                if bt == "list":
                    # Prefer the arm that actually pins an element kind: an empty
                    # literal ("?") shouldn't mask the other arm's real type.
                    be = self._list_el_type(e.body, scope)
                    oe = self._list_el_type(e.orelse, scope)
                    e.list_el_type = be if be not in ("?", "int") else oe
            elif {bt, ot} == {"int", "float"}:
                # Numeric promotion: an int arm widens to float so both land
                # in xmm0 at codegen time.
                e.inferred_type = "float"
            elif "int" in (bt, ot):
                # `X if cond else None` (and the mirror): None reads as the int
                # sentinel, which doubles as serpent's unknown type. Let the
                # concrete arm win so the result keeps a useful type.
                e.inferred_type = ot if bt == "int" else bt
            else:
                raise SemaError(
                    f"conditional expression arms have mismatched types "
                    f"({bt} vs {ot})",
                    e.pos,
                )
            return
        if isinstance(e, A.Call):
            self._check_call(e, scope)
            return
        if isinstance(e, A.ListLit):
            seen: str | None = None
            for el in e.elems:
                self._check_expr(el, scope)
                et = A.expr_type(el)
                if (
                    et not in ("int", "str", "float", "any", "tuple")
                    and not et.startswith("instance:")
                ):
                    raise SemaError(
                        f"list element of type {et} is not supported yet",
                        getattr(el, "pos", e.pos),
                    )
                if et == "any":
                    # Opaque element: compatible with any kind. It pins the list
                    # to "any" only as a fallback, so a later concrete element
                    # still wins (and reads off an all-"any" list stay lenient
                    # rather than degrading to the empty-list "?").
                    if seen is None:
                        seen = "any"
                    continue
                if seen is None or seen == "any":
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
        if isinstance(e, A.Comprehension):
            self._check_expr(e.iter, scope)
            it_t = A.expr_type(e.iter)
            # Element type the loop variable takes from the iterable.
            if it_t == "list":
                el = self._list_el_type(e.iter, scope)
            elif it_t in ("str", "dict"):
                el = "str"  # str chars / dict keys
            elif it_t == "tuple":
                ets = A.tuple_element_types(e.iter)
                el = ets[0] if ets else "int"
            elif it_t == "any":
                el = "any"
            else:
                raise SemaError(
                    f"cannot iterate a {it_t} in a comprehension", e.pos
                )
            # A child scope so the loop variable doesn't leak.
            child = Scope()
            child.types.update(scope.types)
            child.list_el_types.update(scope.list_el_types)
            child.dict_value_types.update(scope.dict_value_types)
            child.tuple_elem_types.update(scope.tuple_elem_types)
            child.add(e.var, el)
            if e.cond is not None:
                self._check_expr(e.cond, child)
            self._check_expr(e.elt, child)
            e.inferred_type = "list"
            e.list_el_type = A.expr_type(e.elt)
            return
        if isinstance(e, A.DictLit):
            for k in e.keys:
                self._check_expr(k, scope)
                if A.expr_type(k) != "str":
                    raise SemaError(
                        "dict keys must be strings (other types not supported yet)",
                        getattr(k, "pos", e.pos),
                    )
            # Dict values must be homogeneous: all int, all str, all float, or
            # all instances of the same class. The value kind is tracked on
            # the DictLit so codegen / iteration can recover it.
            seen_v: str | None = None
            for v in e.values:
                self._check_expr(v, scope)
                vt = A.expr_type(v)
                if (
                    vt not in ("int", "str", "float", "any", "tuple")
                    and not vt.startswith("instance:")
                ):
                    raise SemaError(
                        f"dict value of type {vt} is not supported yet",
                        getattr(v, "pos", e.pos),
                    )
                if vt == "any":
                    continue  # opaque value: compatible with any value kind
                if seen_v is None or seen_v == "any":
                    seen_v = vt
                elif seen_v != vt:
                    raise SemaError(
                        f"mixed dict value types ({seen_v} and {vt}); "
                        "homogeneous dicts only",
                        getattr(v, "pos", e.pos),
                    )
            e.value_type = seen_v if seen_v is not None else "int"
            return
        if isinstance(e, A.TupleLit):
            ets: list[str] = []
            for el in e.elems:
                self._check_expr(el, scope)
                et = A.expr_type(el)
                # Every serpent value is a uniform 8-byte slot, so a tuple may
                # hold any of them — including nested collections (which are
                # pointers). The per-slot kind is tracked for later indexing.
                if (
                    et not in ("int", "str", "float", "any",
                               "tuple", "list", "dict", "set")
                    and not et.startswith("instance:")
                ):
                    raise SemaError(
                        f"tuple element of type {et} is not supported yet",
                        getattr(el, "pos", e.pos),
                    )
                ets.append(et)
            e.elem_types = ets
            return
        if isinstance(e, A.SetLit):
            # A `{a, b, ...}` set literal. Elements are checked but their kind
            # isn't tracked (set membership is the only operation modelled);
            # `expr_type` already reports a SetLit as "set".
            for el in e.elems:
                self._check_expr(el, scope)
            return
        if isinstance(e, A.Subscript):
            self._check_expr(e.obj, scope)
            obj_t = A.expr_type(e.obj)
            if isinstance(e.index, A.Slice):
                if obj_t not in ("str", "list", "any"):
                    raise SemaError(f"slicing not supported on {obj_t}", e.pos)
                if e.index.start is not None:
                    self._check_expr(e.index.start, scope)
                    if A.expr_type(e.index.start) not in ("int", "any"):
                        raise SemaError("slice start must be an int", e.pos)
                if e.index.stop is not None:
                    self._check_expr(e.index.stop, scope)
                    if A.expr_type(e.index.stop) not in ("int", "any"):
                        raise SemaError("slice stop must be an int", e.pos)
                if e.index.step is not None:
                    self._check_expr(e.index.step, scope)
                    if A.expr_type(e.index.step) not in ("int", "any"):
                        raise SemaError("slice step must be an int", e.pos)
                if obj_t == "any":
                    e.inferred_type = "any"
                    return
                if obj_t == "list":
                    # List slice preserves element type. We don't support step
                    # for lists yet — it'd require a non-contiguous copy loop.
                    if e.index.step is not None:
                        raise SemaError("list slice does not support a step yet", e.pos)
                    e.inferred_type = "list"
                    # Propagate element type onto the Subscript so codegen and
                    # downstream `_list_el_type` see the right kind.
                    e.list_el_type = self._list_el_type(e.obj, scope)
                else:
                    e.inferred_type = "str"
                return
            self._check_expr(e.index, scope)
            if obj_t == "list":
                e.inferred_type = self._list_el_type(e.obj, scope)
            elif obj_t == "tuple":
                if A.expr_type(e.index) != "int":
                    raise SemaError("tuple index must be an int", e.pos)
                ets = self._tuple_elem_types(e.obj, scope)
                if not ets:
                    # Unknown per-slot kinds (e.g. a tuple read out of an
                    # unannotated container): stay lenient on indexing.
                    e.inferred_type = "any"
                elif isinstance(e.index, A.IntLit):
                    n = len(ets)
                    idx = e.index.value
                    if idx < -n or idx >= n:
                        raise SemaError(
                            f"tuple index {idx} out of range for {n}-tuple", e.pos
                        )
                    e.inferred_type = ets[idx]
                elif all(t == ets[0] for t in ets):
                    # Dynamic index is only well-typed on a homogeneous tuple.
                    e.inferred_type = ets[0]
                else:
                    raise SemaError(
                        "tuple index must be a constant for a heterogeneous tuple",
                        e.pos,
                    )
            elif obj_t == "dict":
                if A.expr_type(e.index) not in ("str", "any"):
                    raise SemaError("dict keys must be strings", e.pos)
                e.inferred_type = self._dict_value_type(e.obj, scope)
            elif obj_t == "str":
                if A.expr_type(e.index) != "int":
                    raise SemaError("string index must be an int", e.pos)
                e.inferred_type = "str"
            elif obj_t == "any":
                # Indexing an opaque value stays opaque.
                e.inferred_type = "any"
            else:
                raise SemaError(f"cannot index a {obj_t}", e.pos)
            return
        if isinstance(e, A.FString):
            for seg in e.segments:
                self._check_expr(seg, scope)
                t = A.expr_type(seg)
                if t not in ("int", "float", "str", "any") and not t.startswith(
                    "instance:"
                ):
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
                # A field of a user instance carries the type sema inferred for
                # it (str / instance / list / ... ), defaulting to int when the
                # field is unknown (matches the dict's int-default). A field of
                # an external/imported instance is unknown -> "any".
                cls = obj_t.split(":", 1)[1]
                if cls in self.classes:
                    ft = self._resolve_field_type(cls, e.name)
                    e.inferred_type = ft if ft is not None else "int"
                    # Carry the collection element/value kinds so a later
                    # `self.xs[i]` / `for x in self.xs` reads the right kind.
                    if e.inferred_type == "list":
                        e.list_el_type = self._resolve_field_el(cls, e.name)
                    elif e.inferred_type == "dict":
                        e.value_type = self._resolve_field_el(cls, e.name)
                    elif e.inferred_type == "tuple":
                        e.tuple_elem_types = self._resolve_field_tuple(cls, e.name)
                else:
                    e.inferred_type = "any"
                return
            if obj_t in ("module", "any"):
                # Attribute of a module serpent doesn't model (e.g.
                # `sys.stderr`), or of an already-opaque value. Stay lenient.
                e.inferred_type = "any"
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
            obj_t = A.expr_type(e.obj)
            # Bind keyword/vararg arguments for user-class (and super) method
            # calls before checking args, so the rest of the analyzer and
            # codegen see a plain positional call.
            self._maybe_bind_method_args(e, obj_t)
            for a in e.args:
                self._check_expr(a, scope)
            if obj_t == "list":
                el_t = self._list_el_type(e.obj, scope)
                if e.method == "append":
                    if len(e.args) != 1:
                        raise SemaError(
                            f"list.append() takes 1 argument, got {len(e.args)}",
                            e.pos,
                        )
                    arg_t = A.expr_type(e.args[0])
                    if (
                        arg_t not in ("int", "str", "float", "any", "tuple")
                        and not arg_t.startswith("instance:")
                    ):
                        raise SemaError(
                            f"list.append() element of type {arg_t} not supported",
                            e.pos,
                        )
                    if arg_t == "any":
                        # Opaque value: compatible with any element kind, and it
                        # mustn't pin an empty list's type (we don't know it).
                        pass
                    elif el_t == "?":
                        # First append on an empty literal — pin the element type.
                        if isinstance(e.obj, A.Name):
                            scope.list_el_types[e.obj.name] = arg_t
                            e.obj.list_el_type = arg_t
                        el_t = arg_t
                    elif el_t != "any" and arg_t != el_t:
                        raise SemaError(
                            f"list.append() expected {el_t}, got {arg_t}",
                            e.pos,
                        )
                    e.inferred_type = "int"  # returns None ~ 0
                elif e.method == "pop":
                    if e.args:
                        raise SemaError("list.pop() takes no arguments", e.pos)
                    e.inferred_type = el_t if el_t != "?" else "int"
                elif e.method == "extend":
                    # xs.extend(ys): append every element of another list.
                    if len(e.args) != 1:
                        raise SemaError("list.extend() takes 1 argument", e.pos)
                    at = A.expr_type(e.args[0])
                    if at not in ("list", "any"):
                        raise SemaError(
                            f"list.extend() expects a list, got {at}", e.pos
                        )
                    e.inferred_type = "int"  # returns None ~ 0
                else:
                    raise SemaError(f"list has no method {e.method!r}", e.pos)
            elif obj_t == "dict":
                if e.method == "get":
                    # `d.get(k)` or `d.get(k, default)`. With one arg the default
                    # is the None-as-0 sentinel. Result is the dict's value kind
                    # so `cls = self.classes.get(k); cls.parent` resolves.
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError(
                            "dict.get() takes (key) or (key, default)", e.pos
                        )
                    if A.expr_type(e.args[0]) not in ("str", "any"):
                        raise SemaError("dict.get() key must be a str", e.pos)
                    e.inferred_type = self._dict_value_type(e.obj, scope)
                elif e.method == "contains":
                    if len(e.args) != 1:
                        raise SemaError("dict.contains() takes 1 argument", e.pos)
                    if A.expr_type(e.args[0]) != "str":
                        raise SemaError("dict.contains() key must be a str", e.pos)
                    e.inferred_type = "int"
                elif e.method == "keys":
                    if e.args:
                        raise SemaError("dict.keys() takes no arguments", e.pos)
                    e.inferred_type = "list"
                    e.list_el_type = "str"
                elif e.method == "values":
                    if e.args:
                        raise SemaError("dict.values() takes no arguments", e.pos)
                    e.inferred_type = "list"
                    e.list_el_type = "int"
                elif e.method == "update":
                    # d.update(other): merge another dict in. Lenient on the
                    # argument kind; returns None (~0).
                    if len(e.args) != 1:
                        raise SemaError("dict.update() takes 1 argument", e.pos)
                    e.inferred_type = "int"
                else:
                    raise SemaError(f"dict has no method {e.method!r}", e.pos)
            elif obj_t == "str":
                self._check_str_method(e, scope)
                return
            elif obj_t.startswith("super:"):
                # super().method(...) — dispatch against the base class. If the
                # base is external (e.g. Exception), we can't model it, so the
                # call is lenient.
                parent = obj_t.split(":", 1)[1]
                if parent not in self.classes:
                    e.inferred_type = "any"
                    return
                resolved = self._resolve_method(parent, e.method)
                if resolved is None:
                    if self._has_external_base(parent):
                        e.inferred_type = "any"
                        return
                    raise SemaError(
                        f"{parent} has no method {e.method!r}", e.pos
                    )
                _, sig = resolved
                expected = sig.arity - 1
                required = expected - sig.n_defaults
                if not (required <= len(e.args) <= expected):
                    raise SemaError(
                        f"super().{e.method}() takes {required}..{expected} "
                        f"argument(s), got {len(e.args)}",
                        e.pos,
                    )
                if sig.ret_type is not None:
                    ty, el, _val = sig.ret_type
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el
                else:
                    e.inferred_type = "int"
                return
            elif obj_t.startswith("instance:"):
                class_name = obj_t.split(":", 1)[1]
                resolved = self._resolve_method(class_name, e.method)
                if resolved is None:
                    if class_name not in self.classes or self._has_external_base(
                        class_name
                    ):
                        # Either the receiver is an external/imported instance
                        # we don't model at all (e.g. an `argparse.ArgumentParser`
                        # bound to a typed param), or the method lives on an
                        # unmodeled external base (a subclass of an imported
                        # Codegen calling self.emit). Accept it; result is an
                        # opaque value so chained calls stay lenient.
                        e.inferred_type = "any"
                        return
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
                # Return type priority: an inferred `return a, b` tuple shape
                # (so `x, y = obj.m()` unpacks), then an explicit annotation,
                # else int.
                if sig.ret_tuple is not None:
                    e.inferred_type = "tuple"
                    e.tuple_elem_types = list(sig.ret_tuple)
                elif sig.ret_type is not None:
                    ty, el, _val = sig.ret_type
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el
                else:
                    e.inferred_type = "int"
            elif obj_t in ("module", "any"):
                # A method on a module serpent doesn't model (e.g.
                # `argparse.ArgumentParser(...)` — imported but outside the
                # stdlib registry), or on an already-opaque value. Stay lenient;
                # the result is opaque so chains keep type-checking.
                e.inferred_type = "any"
            elif obj_t == "set":
                # Sets model membership + mutation (`add`/`discard`); their
                # methods return None (~int) or an opaque value. Stay lenient.
                e.inferred_type = "int" if e.method in ("add", "discard", "remove") else "any"
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
        # Character-class predicates (0-arg, bool result) used by the lexer.
        "isdigit": ((), "int"),
        "isalpha": ((), "int"),
        "isalnum": ((), "int"),
        "isspace": ((), "int"),
        "isupper": ((), "int"),
        "islower": ((), "int"),
        "isidentifier": ((), "int"),
    }

    def _check_str_method(self, e, scope: Scope) -> None:
        # Methods with non-trivial signatures: split returns list[str]; join
        # consumes a list[str].
        if e.method == "split":
            if len(e.args) > 1:
                raise SemaError("str.split() takes 0 or 1 argument", e.pos)
            if e.args and A.expr_type(e.args[0]) != "str":
                raise SemaError("str.split() separator must be str", e.pos)
            e.inferred_type = "list"
            e.list_el_type = "str"
            return
        if e.method == "splitlines":
            # Optional `keepends` bool arg is accepted and ignored.
            if len(e.args) > 1:
                raise SemaError("str.splitlines() takes 0 or 1 argument", e.pos)
            e.inferred_type = "list"
            e.list_el_type = "str"
            return
        if e.method == "join":
            if len(e.args) != 1:
                raise SemaError("str.join() takes 1 argument", e.pos)
            arg_t = A.expr_type(e.args[0])
            if arg_t not in ("list", "any"):
                raise SemaError("str.join() requires list[str]", e.pos)
            arg_el = self._list_el_type(e.args[0], scope)
            # An opaque element kind ("any") is accepted — we can't prove it's
            # str, but join only ever runs on str elements in practice.
            if arg_el not in ("str", "any"):
                raise SemaError(
                    f"str.join() requires list[str], got list[{arg_el}]", e.pos
                )
            e.inferred_type = "str"
            return
        if e.method in ("strip", "lstrip", "rstrip"):
            # Optional `chars` argument (a str). With no arg, strips whitespace.
            if len(e.args) > 1:
                raise SemaError(
                    f"str.{e.method}() takes 0 or 1 argument", e.pos
                )
            if e.args and A.expr_type(e.args[0]) != "str":
                raise SemaError(
                    f"str.{e.method}() argument must be str", e.pos
                )
            e.inferred_type = "str"
            return
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

    def _maybe_bind_method_args(self, e, obj_t: str) -> None:
        """Bind keyword/vararg args on a user-class method call (or super())
        onto positions. No-op for str/list/dict/external methods, which don't
        take keyword args in serpent's model."""
        sig = None
        if obj_t.startswith("instance:"):
            r = self._resolve_method(obj_t.split(":", 1)[1], e.method)
            sig = r[1] if r else None
        elif obj_t.startswith("super:"):
            r = self._resolve_method(obj_t.split(":", 1)[1], e.method)
            sig = r[1] if r else None
        if sig is None:
            return
        self._bind_args(
            e,
            sig.param_names[1:],
            sig.param_defaults[1:],
            sig.vararg,
            e.pos,
            e.method,
        )

    def _bind_args(self, e, names, defaults, vararg, pos, label) -> None:
        """Rewrite a call's (positional, keyword) arguments into a single
        positional list matching `names`, so codegen sees an ordinary call.

        `names`/`defaults` exclude `self` (callers trim it for methods).
        Keyword args are matched onto positions by name; omitted params fall
        back to their default. With a `*args` parameter (the trailing slot),
        surplus positionals are packed into a ListLit passed in that slot.
        """
        fixed_names = names[:-1] if vararg is not None else names
        fixed_defaults = defaults[:-1] if vararg is not None else defaults
        nfixed = len(fixed_names)
        slots: list = [None] * nfixed
        extra: list = []
        for i, a in enumerate(e.args):
            if i < nfixed:
                slots[i] = a
            elif vararg is not None:
                extra.append(a)
            else:
                raise SemaError(
                    f"{label}() takes {nfixed} argument(s), got {len(e.args)}", pos
                )
        for kname, kexpr in e.kwargs:
            if kname not in fixed_names:
                raise SemaError(
                    f"{label}() got an unexpected keyword argument {kname!r}", pos
                )
            idx = fixed_names.index(kname)
            if slots[idx] is not None:
                raise SemaError(
                    f"{label}() got multiple values for argument {kname!r}", pos
                )
            slots[idx] = kexpr
        for i in range(nfixed):
            if slots[i] is None:
                if fixed_defaults[i] is not None:
                    slots[i] = fixed_defaults[i]
                else:
                    raise SemaError(
                        f"{label}() missing required argument {fixed_names[i]!r}",
                        pos,
                    )
        new_args = list(slots)
        if vararg is not None:
            new_args.append(A.ListLit(elems=extra, pos=pos))
        e.args = new_args
        e.kwargs = []

    def _check_call(self, e: A.Call, scope: Scope) -> None:
        if e.func == "super":
            # super() — only valid inside a method, takes no args, and resolves
            # to the current class's base. The result carries a `super:<Base>`
            # marker so the enclosing MethodCall dispatches against the base.
            if e.args:
                raise SemaError("super() takes no arguments", e.pos)
            if self.current_class is None:
                raise SemaError("super() outside a method", e.pos)
            parent = self.classes[self.current_class].parent
            if parent is None:
                raise SemaError(
                    f"{self.current_class!r} has no base class for super()", e.pos
                )
            e.inferred_type = f"super:{parent}"
            return
        if e.func == "isinstance":
            # isinstance(value, type-or-tuple-of-types) -> bool (int 0/1).
            # The first argument is a normal value; the second is a type
            # position (a class name or a tuple of them) which we accept
            # without type-checking, since classes/unions aren't first-class
            # typed values in serpent's model.
            if len(e.args) != 2:
                raise SemaError(
                    f"isinstance() takes 2 arguments, got {len(e.args)}", e.pos
                )
            self._check_expr(e.args[0], scope)
            e.inferred_type = "int"
            return
        if e.func == "getattr":
            # getattr(obj, "name"[, default]) -> opaque value. The attribute name
            # must be a string literal (serpent instances are dicts keyed by
            # field name; a literal lets codegen intern the key). Result is
            # "any" because the field's static type isn't known.
            if not (2 <= len(e.args) <= 3):
                raise SemaError(
                    f"getattr() takes 2-3 arguments, got {len(e.args)}", e.pos
                )
            if not isinstance(e.args[1], A.StrLit):
                raise SemaError(
                    "getattr() attribute name must be a string literal", e.pos
                )
            self._check_expr(e.args[0], scope)
            if len(e.args) == 3:
                self._check_expr(e.args[2], scope)
            e.inferred_type = "any"
            return
        if e.func == "hasattr":
            # hasattr(obj, "name") -> int 0/1. Same literal-name requirement.
            if len(e.args) != 2:
                raise SemaError(
                    f"hasattr() takes 2 arguments, got {len(e.args)}", e.pos
                )
            if not isinstance(e.args[1], A.StrLit):
                raise SemaError(
                    "hasattr() attribute name must be a string literal", e.pos
                )
            self._check_expr(e.args[0], scope)
            e.inferred_type = "int"
            return
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
            if e.func == "print":
                for a in e.args:
                    if A.expr_type(a) == "tuple":
                        raise SemaError(
                            "cannot print a tuple directly yet; print its elements",
                            getattr(a, "pos", e.pos),
                        )
            # Set the static return type so codegen knows how to interpret it.
            e.inferred_type = {
                "print": "int",
                "len": "int",
                "int": "int",
                "float": "float",
                "str": "str",
                "input": "str",
                "list": "list",
                "set": "set",
                "frozenset": "set",
                "sum": "int",
                "min": "any",
                "max": "any",
                "abs": "any",
                "sorted": "list",
                "reversed": "list",
                "any": "int",
                "all": "int",
                "ord": "int",
                "chr": "str",
                "repr": "str",
            }[e.func]
            if e.func in (
                "set", "frozenset", "sum", "min", "max", "abs", "sorted",
                "reversed", "any", "all", "ord", "chr", "repr",
            ):
                return
            if e.func == "list":
                # list(x) yields a list; carry the source's element kind so
                # later `for el in list(x)` / indexing pick the right register.
                t = A.expr_type(e.args[0])
                if t not in ("list", "tuple", "str", "dict", "any"):
                    raise SemaError(
                        "list() requires a list, tuple, dict, or string", e.pos
                    )
                e.list_el_type = self._list_el_type(e.args[0], scope)
                return
            # Argument-type sanity for builtins that care. An opaque ("any")
            # argument is accepted everywhere — we can't know its real type.
            if e.func == "len":
                t = A.expr_type(e.args[0])
                if t not in ("str", "list", "dict", "tuple", "any"):
                    raise SemaError(
                        "len() requires a string, list, dict, or tuple", e.pos
                    )
            elif e.func == "int":
                t = A.expr_type(e.args[0])
                if t not in ("str", "float", "int", "any"):
                    raise SemaError("int() requires str / float / int", e.pos)
            elif e.func == "float":
                t = A.expr_type(e.args[0])
                if t not in ("str", "int", "float", "any"):
                    raise SemaError("float() requires str / int / float", e.pos)
            elif e.func == "str":
                t = A.expr_type(e.args[0])
                if t not in ("int", "float", "str", "any"):
                    raise SemaError("str() requires int / float / str", e.pos)
            return
        if e.func in self.funcs:
            sig = self.funcs[e.func]
            # Plain positional calls keep the precise arity diagnostics; calls
            # with keyword args or to a `*args` function are validated by the
            # binder instead.
            if sig.vararg is None and not e.kwargs:
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
            # Normalize every call to a complete positional argument list
            # (defaults filled, keyword args placed, varargs packed) so codegen
            # always sees a fixed-shape call.
            self._bind_args(
                e, sig.param_names, sig.param_defaults, sig.vararg, e.pos, e.func
            )
            for a in e.args:
                self._check_expr(a, scope)
            # Return type priority: an inferred `return a, b` tuple shape wins
            # (it carries per-slot kinds); then an explicit return annotation;
            # otherwise int.
            if e.func in self.func_ret_tuple:
                e.inferred_type = "tuple"
                e.tuple_elem_types = list(self.func_ret_tuple[e.func])
            elif sig.ret_type is not None:
                ty, el, _val = sig.ret_type
                e.inferred_type = ty
                if ty == "list" and el is not None:
                    e.list_el_type = el
            else:
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
                # No explicit __init__. A @dataclass-style class (one with
                # declared fields) is constructed field-by-field via the
                # synthesized init — accept the call leniently (full
                # field/keyword validation is post-bootstrap). A class with no
                # fields really does take no arguments.
                if (e.args or e.kwargs) and not self.classes[e.func].fields:
                    raise SemaError(
                        f"{e.func}() has no __init__ and takes no arguments",
                        e.pos,
                    )
                for a in e.args:
                    self._check_expr(a, scope)
                for _kn, kv in e.kwargs:
                    self._check_expr(kv, scope)
                e.inferred_type = f"instance:{e.func}"
                return
            else:
                _, sig = init
                expected = sig.arity - 1
                if sig.vararg is None and not e.kwargs:
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
                self._bind_args(
                    e,
                    sig.param_names[1:],
                    sig.param_defaults[1:],
                    sig.vararg,
                    e.pos,
                    e.func,
                )
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = f"instance:{e.func}"
            return
        # A name bound in the current scope (e.g. a parameter, or a name
        # brought in by `from <mod> import <name>`) used in call position.
        # We can't know its real return type, so treat the result as int.
        # This is what lets imported constructors like `Const(...)` / `Func(...)`
        # and other indirect callables type-check before cross-module
        # resolution lands.
        if e.func in BUILTIN_EXCEPTIONS:
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = f"instance:{e.func}"
            return
        if e.func in scope:
            for a in e.args:
                self._check_expr(a, scope)
            # A capitalized in-scope callable is conventionally an imported
            # class/constructor (`Path(...)`, `Token(...)`); its result is an
            # opaque value so attribute/method access on it stays lenient. (As
            # "any" — not a concrete instance type — so homogeneous containers
            # of several such results don't read as "mixed".)
            e.inferred_type = "any" if e.func[:1].isupper() else "int"
            return
        raise SemaError(f"undefined function {e.func!r}", e.pos)


def analyze(mod: A.Module) -> None:
    SemaAnalyzer(mod).analyze()
