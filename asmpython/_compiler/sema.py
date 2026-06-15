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

import dataclasses
from dataclasses import dataclass, field
from typing import Optional

from . import ast_nodes as A
from .. import stdlib
from ..stdlib import STDLIB_BINDINGS
from ..asmlib import ASMLIB_BINDINGS
from .errors import SemaError


# ---------------------------------------------------------------------------
# Assembly.* compile-time operand validation
# ---------------------------------------------------------------------------

# Every recognised x86-64 register name (lower-case). Used by sema to catch
# typos like "rax2" or "rdx3" before they reach NASM.
_ASM_VALID_REGS: frozenset = frozenset({
    # 64-bit GP
    "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rsp", "rbp", "rip",
    "r8",  "r9",  "r10", "r11", "r12", "r13", "r14", "r15",
    # 32-bit GP
    "eax", "ebx", "ecx", "edx", "esi", "edi", "esp", "ebp", "eip",
    "r8d", "r9d", "r10d","r11d","r12d","r13d","r14d","r15d",
    # 16-bit GP
    "ax",  "bx",  "cx",  "dx",  "si",  "di",  "sp",  "bp",  "ip",
    "r8w", "r9w", "r10w","r11w","r12w","r13w","r14w","r15w",
    # 8-bit GP
    "al",  "ah",  "bl",  "bh",  "cl",  "ch",  "dl",  "dh",
    "sil", "dil", "spl", "bpl",
    "r8b", "r9b", "r10b","r11b","r12b","r13b","r14b","r15b",
    # XMM
    "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5",
    "xmm6", "xmm7", "xmm8", "xmm9", "xmm10","xmm11",
    "xmm12","xmm13","xmm14","xmm15",
    # YMM
    "ymm0", "ymm1", "ymm2", "ymm3", "ymm4", "ymm5",
    "ymm6", "ymm7", "ymm8", "ymm9", "ymm10","ymm11",
    "ymm12","ymm13","ymm14","ymm15",
    # MMX
    "mm0","mm1","mm2","mm3","mm4","mm5","mm6","mm7",
    # Segment
    "cs", "ds", "es", "fs", "gs", "ss",
})

# Size keywords that may prefix a memory operand ("qword [rbp-8]").
_ASM_SIZE_KWS: frozenset = frozenset({
    "byte", "word", "dword", "qword", "oword", "tword",
    "xmmword", "ymmword", "zmmword",
})


def _asm_operand_looks_like_bad_register(s: str) -> bool:
    """Return True iff `s` (already lower-cased) looks like an intended register
    name but is not in the valid set.  Only fires on register-shaped tokens so
    label names like 'my_loop' are not flagged."""
    # Must be purely alphanumeric — no brackets, operators, spaces, underscores.
    for ch in s:
        if not (ch.isalpha() or ch.isdigit()):
            return False
    # Must start with a register-prefix pattern.
    if s.startswith("xmm") or s.startswith("ymm") or s.startswith("zmm"):
        return s not in _ASM_VALID_REGS
    if len(s) >= 2 and s[0] == "r":
        # r + digit(s) → intended as r8-r15 range
        if s[1].isdigit():
            return s not in _ASM_VALID_REGS
        # rax / rbx / rcx … style: at least 2 alpha chars after 'r'
        if s[1].isalpha():
            return s not in _ASM_VALID_REGS
    if len(s) >= 2 and s[0] == "e" and s[1].isalpha():
        return s not in _ASM_VALID_REGS
    return False


def _check_asm_operand_lit(val: str, pos: object) -> None:
    """Validate a single Assembly method operand that is a string literal.

    Raises SemaError for:
    - empty operands
    - operands that look like register names but are not valid x86-64 registers
      (e.g. "rax2", "rdx3", "eax9")

    Everything else (memory refs, immediates, labels, complex expressions) is
    accepted — NASM will catch genuine syntax errors when it assembles.
    """
    s = val.strip()
    if not s:
        raise SemaError("Assembly: operand must not be an empty string", pos)

    lo = s.lower()

    # Memory reference or complex expression → always accept
    if "[" in lo or "+" in lo or "-" in lo or "*" in lo or " " in lo or ":" in lo:
        return

    # Strip a leading size keyword ("qword", "byte ptr", etc.)
    for kw in _ASM_SIZE_KWS:
        if lo == kw or lo.startswith(kw + " ") or lo.startswith(kw + "["):
            return

    # Numeric immediate (decimal, hex, octal, binary, possibly with leading '-')
    stripped = lo.lstrip("-")
    if stripped.startswith("0x") or stripped.startswith("0b") or stripped.startswith("0o"):
        return
    if stripped and all(c.isdigit() for c in stripped):
        return

    # Known good register → fine
    if lo in _ASM_VALID_REGS:
        return

    # rel / abs RIP-relative prefix
    if lo.startswith("rel") or lo.startswith("abs"):
        return

    # Flag a register-shaped name that isn't in the valid set
    if _asm_operand_looks_like_bad_register(lo):
        raise SemaError(
            f"Assembly: {val!r} is not a recognised x86-64 register", pos
        )

    # Anything else (label, symbol) → accept


# ---------------------------------------------------------------------------
# Builtins we accept. Values describe required arg-count range.
# ---------------------------------------------------------------------------

# Builtins we accept. Values describe required arg-count range.
BUILTINS: dict[str, tuple[int, int]] = {
    "print": (0, 64),  # 0 args = just a newline; >0 = space-separated
    "len": (1, 1),
    "int": (1, 2),  # int(x) or int(s, base) — base parsing for "0x.."/"0o.." etc.
    "float": (1, 1),
    "str": (1, 1),
    "input": (0, 1),
    "list": (1, 1),  # list(iterable) -> shallow copy as a list
    "tuple": (1, 1),  # tuple(iterable) -> shallow copy (shares the list layout)
    "bool": (1, 1),  # bool(x) -> 0/1 truthiness
    "dict": (0, 1),  # dict() / dict(other) -> shallow copy as a dict
    "set": (0, 1),  # set() / set(iterable)
    "frozenset": (0, 1),  # frozenset() / frozenset(iterable)
    "sum": (1, 2),  # sum(iterable[, start])
    "min": (1, 64),  # min(iterable) or min(a, b, ...)
    "max": (1, 64),  # max(iterable) or max(a, b, ...)
    "abs": (1, 1),
    "round": (1, 2),  # round(x[, ndigits]) -> int (ndigits ignored for now)
    "pow": (2, 3),    # pow(base, exp[, mod]) -> int
    "sorted": (1, 1),  # sorted(iterable) (key/reverse via kwargs)
    "reversed": (1, 1),
    "any": (1, 1),
    "all": (1, 1),
    "ord": (1, 1),
    "chr": (1, 1),
    "repr": (1, 1),
    "type": (1, 1),  # type(x) -> opaque type object (`.__name__` reads lenient)
    "id": (1, 1),  # id(x) -> the object's pointer value (unique per object)
    "open": (1, 3),  # open(file[, mode[, encoding]]) -> file object (opaque)
    "vars": (0, 1),  # vars([obj]) -> dict
    "dir": (0, 1),  # dir([obj]) -> list
    "callable": (1, 1),  # callable(obj) -> bool
    "setattr": (3, 3),  # setattr(obj, name, value)
    "delattr": (2, 2),  # delattr(obj, name)
    "iter": (1, 2),  # iter(obj) -> iterator
    "next": (1, 2),  # next(iterator[, default]) -> any
    "map": (2, 64),  # map(func, *iterables) -> iterator
    "filter": (2, 2),  # filter(func, iterable) -> iterator
    "format": (1, 2),  # format(value[, spec]) -> str
    "hex": (1, 1),  # hex(x) -> str
    "oct": (1, 1),  # oct(x) -> str
    "bin": (1, 1),  # bin(x) -> str
    "divmod": (2, 2),  # divmod(a, b) -> (a // b, a % b)
    "hash": (1, 1),  # hash(x) -> int
    "issubclass": (2, 2),
    "bytes": (0, 2),     # bytes() / bytes(n) / bytes(str) -> list[int]
    "bytearray": (0, 2), # bytearray() / bytearray(n) / bytearray(str) -> list[int]
}


# Builtin exception classes. asmpython's exception runtime is string-message
# based, but the *front end* must accept idiomatic `raise ValueError(msg)` and
# bare `raise NotImplementedError`. These names resolve as class objects and,
# when called, yield an (external) instance.
BUILTIN_EXCEPTIONS: frozenset[str] = frozenset({
    "BaseException",
    "Exception",
    "SystemExit",
    "KeyboardInterrupt",
    "RuntimeError",
    "NotImplementedError",
    "ValueError",
    "TypeError",
    "NameError",
    "AttributeError",
    "KeyError",
    "IndexError",
    "LookupError",
    "StopIteration",
    "ArithmeticError",
    "ZeroDivisionError",
    "OverflowError",
    "AssertionError",
    "ImportError",
    "OSError",
    "IOError",
    "FileNotFoundError",
})


# Interpreter-only `<module>.<method>` calls: features that require a live
# Python interpreter (dynamic import / code execution by string) and so cannot
# be compiled to native code. These are in the excluded 0.1% of the language.
# Rejected with a clear, located message rather than letting them slip through
# the module-leniency path and explode in codegen with a raw traceback.
INTERPRETER_ONLY_METHODS: frozenset[tuple[str, str]] = frozenset({
    ("importlib", "import_module"),
    ("importlib", "reload"),
    ("imp", "load_module"),
})

# Interpreter-only *builtins* (bare calls, not module methods).
INTERPRETER_ONLY_BUILTINS: frozenset[str] = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
})

# Binary operator -> (forward dunder, reflected dunder). A user class can
# overload `a <op> b` by defining the forward method on `a`'s class, or the
# reflected method on `b`'s class (used when `a`'s type doesn't define the
# forward method, mirroring Python's `NotImplemented` fallback — but since
# asmpython doesn't model `NotImplemented`, the forward method wins whenever
# it exists). E.g. `Path("a") / "b"` resolves `Path.__truediv__`.
DUNDER_BINOP: dict[str, tuple[str, str]] = {
    "+": ("__add__", "__radd__"),
    "-": ("__sub__", "__rsub__"),
    "*": ("__mul__", "__rmul__"),
    "/": ("__truediv__", "__rtruediv__"),
    "//": ("__floordiv__", "__rfloordiv__"),
    "%": ("__mod__", "__rmod__"),
    "**": ("__pow__", "__rpow__"),
    "&": ("__and__", "__rand__"),
    "|": ("__or__", "__ror__"),
    "^": ("__xor__", "__rxor__"),
    "<<": ("__lshift__", "__rlshift__"),
    ">>": ("__rshift__", "__rrshift__"),
    "@": ("__matmul__", "__rmatmul__"),
}

# Dunder methods whose second parameter ("other") conventionally holds another
# instance of the same class — every forward/reflected arithmetic dunder plus
# the rich-comparison dunders. An unannotated `other` on one of these methods
# is seeded as `instance:<the enclosing class>` (rather than the usual "int"
# default for unannotated params), so `other.field` resolves via the same
# dict-based attribute access as `self.field`. This is the overwhelmingly
# common convention (`def __add__(self, other): return self.x + other.x`); a
# method whose `other` is genuinely a different type just needs an annotation,
# same as any other parameter.
DUNDER_SAME_TYPE_OTHER: frozenset[str] = frozenset(
    {fwd for fwd, _rfl in DUNDER_BINOP.values()}
    | {rfl for _fwd, rfl in DUNDER_BINOP.values()}
    | {"__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__"}
)


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
    # When ret_type is ("list", "tuple", ...) from a `-> list[tuple[T1,T2]]`
    # annotation, the per-slot kinds ["T1","T2"] (else None). Lets call sites
    # of `for a, b in <list[tuple[T1,T2]]>` type each unpack target.
    ret_list_tuple_types: object = None
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
    # Decorator identities for methods (["staticmethod"] / ["classmethod"]).
    decorators: list = field(default_factory=list)
    # True when every reachable `return` in the body is a bare `return self`
    # (and at least one exists), and the method has no explicit return-type
    # annotation. Lets call sites of e.g. `__enter__` (which conventionally
    # `return self`) infer `instance:<ClassName>` instead of defaulting to
    # `int`. Mirrors `ret_tuple`'s body-scanning approach.
    returns_self: bool = False


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
    # Property name -> mangled setter method name (e.g. "x" -> "x__setter"),
    # populated for methods decorated `@x.setter`. The setter itself is also
    # registered in `methods` under its mangled name so normal method
    # resolution/dispatch (incl. virtual dispatch) handles it unchanged.
    setters: dict[str, str] = field(default_factory=dict)
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
    # For names typed "list" whose elements are themselves containers
    # (list[dict] / list[list]), the common value/element kind of those nested
    # containers — so `xs[i][k]` and `for x in xs: x[k]` recover the leaf type.
    list_el_value_types: dict[str, str] = field(default_factory=dict)
    # For names typed "list" whose elements are tuples (list[tuple]), the common
    # per-slot element kinds of those tuples — so `xs[i][0]` and
    # `for a, b in xs` recover the slot types. Empty when unknown.
    list_el_tuple_types: dict[str, list[str]] = field(default_factory=dict)
    # For names typed "dict", value kind. Keys are always str in v1.
    dict_value_types: dict[str, str] = field(default_factory=dict)
    # For names typed "dict" whose value kind is itself a container, the common
    # inner value/element kind of those nested containers — so `d[k][k2]` reads
    # the leaf type. "int"/absent when unknown.
    dict_inner_value_types: dict[str, str] = field(default_factory=dict)
    # For names typed "dict" whose value kind is "tuple", the common per-slot
    # element kinds of those value tuples — so `d.values()` / `d.items()` can
    # type unpacked targets (`for k, v in d.items()`). Absent when unknown.
    dict_value_tuple_types: dict[str, list[str]] = field(default_factory=dict)
    # For names typed "tuple", the per-slot element kinds.
    tuple_elem_types: dict[str, list[str]] = field(default_factory=dict)
    # For names typed "int" that were last assigned a bool-valued expression
    # (see A.is_bool_expr) — lets print()/str()/f-strings render "True"/"False".
    bool_flags: dict[str, bool] = field(default_factory=dict)
    # For names typed "int" that were last assigned `None` (see A.is_none_expr)
    # — lets print()/str()/f-strings render "None".
    none_flags: dict[str, bool] = field(default_factory=dict)

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
        el_value_type: str | None = None,
        el_tuple_types: list[str] | None = None,
        value_type: str | None = None,
        inner_value_type: str | None = None,
        value_tuple_types: list[str] | None = None,
        tuple_types: list[str] | None = None,
        is_bool: bool = False,
        is_none: bool = False,
    ) -> None:
        self.types[name] = ty
        self.bool_flags[name] = is_bool
        self.none_flags[name] = is_none
        if ty == "list" and el_type is not None:
            self.list_el_types[name] = el_type
        if ty == "list" and el_value_type is not None:
            self.list_el_value_types[name] = el_value_type
        if ty == "list" and el_tuple_types:
            self.list_el_tuple_types[name] = el_tuple_types
        if ty == "dict" and value_type is not None:
            self.dict_value_types[name] = value_type
        if ty == "dict" and inner_value_type is not None:
            self.dict_inner_value_types[name] = inner_value_type
        if ty == "dict" and value_tuple_types:
            self.dict_value_tuple_types[name] = value_tuple_types
        if ty == "tuple" and tuple_types is not None:
            self.tuple_elem_types[name] = tuple_types

    def __contains__(self, name: str) -> bool:
        return name in self.types


def _count_defaults(defaults: list) -> int:
    """How many trailing parameters carry a default (None = no default).
    A plain loop — `sum(genexpr)` is outside the compilable subset."""
    n = 0
    for d in defaults:
        if d is not None:
            n = n + 1
    return n


def _all_same(items: list) -> bool:
    """True if every element equals the first (vacuously true when empty).
    A plain loop — `all(genexpr)` is outside the compilable subset."""
    for x in items:
        if x != items[0]:
            return False
    return True


def _load_module(name: str) -> dict:
    """Return the BINDINGS dict for stdlib module `name`.

    Looks the name up in the static `STDLIB_BINDINGS` registry — a plain dict,
    *not* a dynamic `importlib.import_module(name)`. Dynamic import by string is
    an interpreter-only feature the compiler can't compile, so the compiler must
    not use it on itself; a static lookup keeps `sema.py` inside the compilable
    subset (needed for self-hosting). Unknown names raise SemaError, which
    callers catch to fall back to opaque handling for non-stdlib imports.
    """
    # Accept the fully-qualified package path too, so the compiler's own
    # `from asmpython.stdlib import os` / `import asmpython.stdlib.os` resolve to
    # the same FFI bindings as a user's bare `import os`. (The compiler imports
    # its stdlib by full path to avoid clashing with CPython's stdlib at compile
    # time; both spellings must reach the one binding set.)
    key = name
    for prefix in ("asmpython.stdlib.", "asmpython._stdlib.", "stdlib.",
                   "asmpython.asmlib.", "asmlib."):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    if key in STDLIB_BINDINGS:
        return STDLIB_BINDINGS[key]
    if key in ASMLIB_BINDINGS:
        return ASMLIB_BINDINGS[key]
    raise SemaError(f"no such module: {name!r}")


class SemaAnalyzer:
    def __init__(self, mod: A.Module, *, source_dir=None) -> None:
        self.mod = mod
        # Directory of the source file, for resolving include("pkg") against a
        # sibling `<pkg>.asmpkg`. None outside a real compile.
        self.source_dir = source_dir
        # Loaded assembly packages, keyed by package name (dedup repeated
        # includes). Their exports become callable symbols.
        self.asm_packages: dict[str, object] = {}
        self.funcs: dict[str, FuncSig] = {}
        self.classes: dict[str, ClassSig] = {}
        # Variable name -> return type of the lambda bound to it, so an indirect
        # call `f(...)` on a name-bound lambda gets the right result type.
        self.lambda_rets: dict[str, str] = {}
        # Module-level names (imports + top-level assignments). Populated by
        # analyze() before function/method bodies are checked.
        self.global_scope: Scope = Scope()
        self.loop_depth = 0
        self.in_function: Optional[str] = None
        self.in_lifted: bool = False  # True when checking a lifted nested func
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
        # (qualified_name, param_index) -> (ty, el, val, tup) for parameters
        # with no annotation and no default, inferred from literal-typed
        # arguments at call sites. `qualified_name` is a function's plain name,
        # or "ClassName.method_name" for a method. See
        # `_infer_unannotated_params`.
        self.inferred_param_types: dict[tuple[str, int], tuple] = {}

    def _has_external_base(self, class_name: str) -> bool:
        """True if `class_name` or any ancestor inherits from a base that isn't
        a user-defined class (a builtin like Exception, or a name imported from
        another module). Such a base may supply methods/fields asmpython can't
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

    def _class_var_type(self, class_name: str, var: str) -> "str | None":
        """Static type of a class-level variable `ClassName.var` from its
        default expression, or None if the class has no such class var. Only
        plain classes contribute static class vars (a @dataclass's class vars
        are per-instance fields)."""
        for c in self.mod.classes:
            if c.name != class_name:
                continue
            if getattr(c, "is_dataclass", False):
                return None
            for cv in getattr(c, "class_vars", []) or []:
                cvname, _annot, cvdefault = cv
                if cvname == var and cvdefault is not None:
                    return A.expr_type(cvdefault)
        return None

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

    def _resolve_setter(self, class_name: str, prop_name: str) -> Optional[str]:
        """Walk parent chain to find a `@<prop_name>.setter` for `prop_name`.

        Returns the setter's mangled method name (e.g. "x__setter"), or None.
        """
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls = self.classes.get(cur)
            if cls is None:
                return None
            if prop_name in cls.setters:
                return cls.setters[prop_name]
            cur = cls.parent
        return None

    def _check_exc_type_name(self, name: str, pos) -> None:
        """Validate that `name` (from an `except <name>:` clause) refers to a
        builtin exception or a user class deriving from one."""
        if name in BUILTIN_EXCEPTIONS:
            return
        if name in self.classes and self._is_exception_class(name):
            return
        raise SemaError(
            f"'{name}' is not an exception type", pos
        )

    def _is_exception_class(self, class_name: str) -> bool:
        """True if `class_name` derives (transitively) from a builtin exception.
        Such a class inherits `Exception.__init__`, so `MyError("msg")` is valid
        even without an explicit `__init__` or declared fields."""
        cur = class_name
        seen: set = set()
        while cur is not None and cur not in seen:
            if cur in BUILTIN_EXCEPTIONS:
                return True
            seen.add(cur)
            cls = self.classes.get(cur)
            if cls is None:
                # Parent isn't a user class; it's an exception only if its name
                # is a builtin exception (checked at the top of the next loop).
                return cur in BUILTIN_EXCEPTIONS
            cur = cls.parent
        return False

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
                    ty, el, val, _tup, _elval = r
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
                    r = self._resolve_annot(annot)  # type: ignore
                    if r is not None:
                        pinfo[p] = r
                    elif i < len(m.defaults) and m.defaults[i] is not None:
                        pinfo[p] = (A.expr_type(m.defaults[i]), None, None, None, None)  # type: ignore
                    else:
                        inferred = self.inferred_param_types.get((f"{c.name}.{m.name}", i))
                        if inferred is not None:
                            pinfo[p] = inferred
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
                r = self._resolve_annot(getattr(s, "annot", None))  # type: ignore
                if r is not None:
                    ty, el, val, tup, _elval = r
                else:
                    raw = self._static_value_info(s.value, pinfo)
                    ty, el, val, tup = raw[0], raw[1], raw[2], raw[3]
                    _elval = raw[4] if len(raw) > 4 else None
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
                for _types, _bind, hbody in s.extra_handlers:
                    self._scan_field_assigns(hbody, sig, pinfo)
                self._scan_field_assigns(s.else_body, sig, pinfo)
                self._scan_field_assigns(s.finally_body, sig, pinfo)

    def _literal_arg_type(self, value):
        """(ty, el, val, tuple) for an expression whose type is knowable from
        its syntax alone, independent of scope -- or None if it depends on a
        name binding (e.g. a bare variable reference). Used both by
        `_static_value_info` (for `self.x = <value>` field inference) and by
        `_infer_unannotated_params` (for call-site argument inference)."""
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
        if isinstance(value, A.Call) and value.func in self.classes:
            return (f"instance:{value.func}", None, None, None)
        return None

    def _static_value_info(self, value, pinfo: dict):
        """Best-effort (ty, el, val, tuple) of an assigned value, used for field
        inference before full body analysis (so it can't rely on stamped
        inferred_type). Covers the dataclass-style cases that matter. `el`/`val`
        are the list-element / dict-value kinds; `tuple` the per-slot kinds."""
        lit = self._literal_arg_type(value)
        if lit is not None:
            return lit
        if isinstance(value, A.Name):
            return pinfo.get(value.name, ("int", None, None, None))
        return ("int", None, None, None)

    # ---- call-site argument type inference for unannotated parameters ------

    def _collect_calls(self, node, out: list) -> None:
        """Recursively collect every `A.Call`/`A.MethodCall` reachable from
        `node` (a statement, expression, or list of either), via a generic
        dataclass-field walk. Used by `_infer_unannotated_params` to find every
        call site of every function/method in the module, regardless of how
        deeply it's nested in expressions."""
        if isinstance(node, (A.Call, A.MethodCall)):
            out.append(node)
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                self._collect_calls(getattr(node, f.name, None), out)
        elif isinstance(node, (list, tuple)):
            for item in node:
                self._collect_calls(item, out)

    def _infer_unannotated_params(self) -> None:
        """For function/method parameters with no type annotation and no
        default, scan every call site in the module for arguments whose type
        is knowable from syntax alone (`_literal_arg_type`: literals,
        f-strings, constructor calls) and, if every such call site agrees on a
        single non-int type, adopt it as the parameter's type.

        Without this, idiomatic Python -- which rarely annotates parameters --
        has every unannotated parameter (and any `self.x = param` field it
        feeds) silently default to `int`, so e.g. `def __init__(self, name):
        self.name = name` called as `Cls("a")` would treat `self.name` as an
        int and print its pointer value instead of "a". A parameter with no
        literal-typed call sites, or with conflicting literal types, keeps the
        existing `int` default -- callers needing a different type still
        annotate explicitly, same as before."""
        calls: list = []
        self._collect_calls(self.mod.body, calls)
        for f in self.mod.funcs:
            self._collect_calls(f.body, calls)
        for c in self.mod.classes:
            for m in c.methods:
                self._collect_calls(m.body, calls)

        for f in self.mod.funcs:
            sites = [c for c in calls if isinstance(c, A.Call) and c.func == f.name]
            self._infer_call_target_params(f.name, f, sites, start=0)

        for c in self.mod.classes:
            for m in c.methods:
                if m.name == "__init__":
                    sites = [
                        c2 for c2 in calls if isinstance(c2, A.Call) and c2.func == c.name
                    ]
                else:
                    # Matched by method name only (the receiver's static type
                    # isn't known yet at this pre-pass). If another class has a
                    # same-named method with conflicting argument types, the
                    # mismatch just falls back to `int` as before -- no new
                    # miscompile.
                    sites = [
                        mc for mc in calls
                        if isinstance(mc, A.MethodCall) and mc.method == m.name
                    ]
                self._infer_call_target_params(f"{c.name}.{m.name}", m, sites, start=1)

    def _infer_call_target_params(self, qualname: str, fn, sites: list, start: int) -> None:
        """Infer types for `fn`'s parameters at index >= `start` (0 for plain
        functions, 1 for methods to skip `self`) from `sites` (the `A.Call`/
        `A.MethodCall` nodes invoking it), storing results in
        `self.inferred_param_types`. See `_infer_unannotated_params`."""
        for i, p in enumerate(fn.params):
            if i < start:
                continue
            annot = fn.param_types[i] if i < len(fn.param_types) else None
            if self._resolve_annot(annot) is not None:  # type: ignore
                continue
            if i < len(fn.defaults) and fn.defaults[i] is not None:
                continue
            candidates: set = set()
            found_any = False
            arg_idx = i - start
            for site in sites:
                args = site.args
                if arg_idx < len(args):
                    arg = args[arg_idx]
                else:
                    arg = next((v for n, v in getattr(site, "kwargs", []) if n == p), None)
                    if arg is None:
                        continue
                lit = self._literal_arg_type(arg)
                if lit is None:
                    continue
                found_any = True
                candidates.add(lit)
            if found_any and len(candidates) == 1:
                self.inferred_param_types[(qualname, i)] = next(iter(candidates))

    # ---- return-type inference for unannotated functions/methods ----------

    def _infer_unannotated_returns(self) -> None:
        """For functions/methods with no return-type annotation and no
        inferred tuple-return shape (`ret_tuple`), scan `return` statements
        and -- if every reachable one has a value, every value's type is
        statically knowable, and they all agree -- adopt that as `ret_type`.
        Mirrors `ret_tuple`'s body-scanning precedent for the scalar case,
        e.g. `def f(x): return x` called as `f("hi")` makes `f("hi")` a str
        result instead of defaulting to int (using the parameter type
        `_infer_unannotated_params` just determined for `x`)."""
        for f in self.mod.funcs:
            sig = self.funcs[f.name]
            if sig.ret_type is not None or sig.ret_tuple is not None:
                continue
            ty = self._infer_return_type(f, f.name)
            if ty is not None:
                sig.ret_type = ty
        for c in self.mod.classes:
            for m in c.methods:
                sig = self.classes[c.name].methods[m.name]
                if (
                    sig.ret_type is not None
                    or sig.ret_tuple is not None
                    or sig.returns_self
                ):
                    continue
                ty = self._infer_return_type(m, f"{c.name}.{m.name}")
                if ty is not None:
                    sig.ret_type = ty

    def _infer_return_type(self, fn, qualname: str):
        """(ty, el, val) for every reachable `return` in `fn.body`, if all
        have a value and those values' types are statically knowable
        (`_literal_arg_type`, or a reference to one of `fn`'s parameters whose
        type is known) and agree -- else None. Helper for
        `_infer_unannotated_returns`."""
        returns: list = []
        self._collect_returns(fn.body, returns)
        if not returns:
            return None
        types: set = set()
        for r in returns:
            if r.value is None:
                return None  # bare `return` mixed in: ambiguous
            lit = self._literal_arg_type(r.value)
            if lit is None and isinstance(r.value, A.Name) and r.value.name in fn.params:
                j = fn.params.index(r.value.name)
                annot = fn.param_types[j] if j < len(fn.param_types) else None
                resolved = self._resolve_annot(annot)  # type: ignore
                if resolved is not None:
                    lit = resolved
                elif j < len(fn.defaults) and fn.defaults[j] is not None:
                    lit = (A.expr_type(fn.defaults[j]), None, None, None, None)
                else:
                    lit = self.inferred_param_types.get((qualname, j))
            if lit is None:
                return None
            types.add((lit[0], lit[1], lit[2]))
        if len(types) == 1:
            return next(iter(types))
        return None

    def _static_value_type(self, value, ptypes: dict) -> str:
        """Just the type half of `_static_value_info` (kept for callers that
        only need the field's base type)."""
        ty, _el, _val, _tup = self._static_value_info(value, ptypes)
        return ty

    # ---- parameter annotation resolution ------------------------------------

    def _resolve_scalar_annot(self, base) -> str:
        """An element/value base from an annotation -> a asmpython scalar type."""
        if base is None or base == "any":
            # A bare `list` / `dict` with no element annotation, or an element
            # the parser already collapsed to "any" (e.g. `list[ClassDef]`,
            # where the element class is external/opaque): the element kind is
            # unknown, so stay opaque ("any") rather than guessing int. That
            # keeps `xs.append(<anything>)`, element reads, and `for x in xs`
            # lenient instead of mis-typing the element as int.
            return "any"
        if base in ("int", "str", "float"):
            return base
        if base in ("list", "dict", "tuple"):
            # A nested collection element/value (`dict[str, list[str]]`): every
            # value is an 8-byte pointer, so the container kind passes through.
            return base
        if base in ("set", "frozenset"):
            return "set"
        if base == "object":
            return "any"
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

    def _resolve_annot(self, annot):
        """Turn a parser annotation descriptor (base, el) into
        (ty, el_type, value_type, tuple_types, el_value_type), or None if it
        doesn't constrain the type (so the caller falls back to default
        inference). `annot` is a (base, el) tuple or None."""
        if annot is None:
            return None
        base, el = annot
        if base in ("int", "str", "float"):
            return (base, None, None, None, None)
        if base == "list":
            if isinstance(el, tuple) and el[0] == "tuple":
                # list[tuple[T1, T2, ...]]: el is ("tuple", [base1, base2, ...])
                # (see parser._normalize_annot) -- resolve each slot's kind so
                # `for a, b in <list[tuple[T1,T2]]>` can type each target.
                slot_types = [self._resolve_scalar_annot(b) for b in el[1]]
                return ("list", "tuple", None, slot_types, None)
            if isinstance(el, tuple) and el[0] == "list":
                # list[list[T]]: el is ("list", inner_el_name)
                # Propagate the leaf kind via el_value_type so
                # `for row in matrix: row[i]` recovers the element type.
                inner_el = self._resolve_scalar_annot(el[1])
                return ("list", "list", None, None, inner_el)
            if isinstance(el, tuple) and el[0] == "dict":
                # list[dict[K,V]]: el is ("dict", val_el_name)
                # Propagate the value kind via el_value_type so
                # `for d in dicts: d[key]` recovers the value type.
                inner_val = self._resolve_scalar_annot(el[1])
                return ("list", "dict", None, None, inner_val)
            return ("list", self._resolve_scalar_annot(el), None, None, None)
        if base == "dict":
            return ("dict", None, self._resolve_scalar_annot(el), None, None)
        if base == "tuple":
            # Annotations don't give per-slot kinds; leave them unknown.
            return ("tuple", None, None, [], None)
        if base in ("set", "frozenset"):
            # A `set`-annotated value: type it as a set so membership and the
            # set methods (`add`/`discard`/`remove`/`update`) resolve, rather
            # than falling through to the int default.
            return ("set", None, None, None, None)
        if base == "any":
            # An explicit opaque annotation (`object`, `Any`, or a genuine
            # multi-type union the parser collapsed to "any"): constrain the
            # value to the lenient "any" type rather than leaving it to default
            # to int. Lets a `-> str | list` method type its result usefully.
            return ("any", None, None, None, None)
        if base == "none":
            return None
        if base in self.classes:
            return (f"instance:{base}", None, None, None, None)
        # An external / imported class annotation (`Token`, `A.IntLit`,
        # `FuncInfo`). We can't see its methods or fields, so model it as an
        # opaque instance: attribute and method access against it are checked
        # leniently (see _check_expr's Attr / MethodCall handling). The leaf of
        # a dotted path is the class-ish name.
        leaf = base.split(".")[-1]
        if leaf[:1].isupper():
            return (f"instance:{leaf}", None, None, None, None)
        # A lowercase unknown name (a type alias we don't model) — don't
        # constrain; the body's usage decides what's legal.
        return None

    def _seed_param(self, scope: Scope, name: str, annot, default_expr, inferred=None) -> None:
        """Add a parameter to `scope`, typing it from its annotation if
        present, otherwise from a literal default, otherwise from
        `inferred` (a (ty, el, val, tup) tuple from
        `_infer_unannotated_params`, or None), otherwise int."""
        resolved = self._resolve_annot(annot)
        if resolved is not None:
            ty, el, val, tup, elval = resolved
            scope.add(name, ty, el_type=el, value_type=val, tuple_types=tup,
                      el_value_type=elval)
            return
        if default_expr is not None:
            scope.add(name, A.expr_type(default_expr))
            return
        if inferred is not None:
            ty, el, val, tup = inferred[:4]
            elval = inferred[4] if len(inferred) > 4 else None
            scope.add(name, ty, el_type=el, value_type=val, tuple_types=tup,
                      el_value_type=elval)
            return
        scope.add(name, "int")

    def _seed_globals_into(self, scope: Scope) -> None:
        """Copy module-level names (and their tracked types) into a fresh
        function/method scope so global reads resolve. Locals declared later in
        the body simply overwrite these entries, giving locals precedence."""
        g = self.global_scope
        scope.types.update(g.types)
        scope.list_el_types.update(g.list_el_types)
        scope.dict_value_types.update(g.dict_value_types)
        scope.dict_inner_value_types.update(g.dict_inner_value_types)
        scope.dict_value_tuple_types.update(g.dict_value_tuple_types)
        scope.tuple_elem_types.update(g.tuple_elem_types)

    # ---- entry --------------------------------------------------------------

    def _inject_assembly_class_if_needed(self) -> None:
        """If the module imports Assembly from asmpython.assembly, parse the
        stdlib source and splice the Assembly ClassDef into self.mod.classes
        so it participates in normal class collection and codegen."""
        needed = False
        for stmt in self.mod.body:
            if (isinstance(stmt, A.FromImport)
                    and stmt.level == 0
                    and stmt.module in ("asmpython.assembly", "assembly")
                    and "Assembly" in stmt.names):
                needed = True
                break
        if not needed:
            return
        if any(c.name == "Assembly" for c in self.mod.classes):
            return
        try:
            import os as _os
            _here = _os.path.dirname(_os.path.abspath(__file__))
            _root = _os.path.dirname(_here)
            _asm_init = _os.path.join(_root, "stdlib", "assembly", "__init__.py")
            with open(_asm_init, encoding="utf-8") as _fh:
                _src = _fh.read()
            from .lexer import Lexer as _Lx
            from .parser import Parser as _Pr
            _asm_mod = _Pr(_Lx(_src).tokenize()).parse()
            for c in _asm_mod.classes:
                if c.name == "Assembly":
                    self.mod.classes.insert(0, c)
                    return
        except Exception:
            pass

    def analyze(self) -> None:
        # Inject stdlib Assembly class if the user imported it, so the
        # constructor and method calls resolve through the normal class path.
        self._inject_assembly_class_if_needed()
        # First pass: collect function signatures so forward references resolve.
        for f in self.mod.funcs:
            if f.name in self.funcs:
                raise SemaError(f"function {f.name!r} redefined", f.pos)
            if f.name in BUILTINS:
                raise SemaError(
                    f"cannot redefine builtin {f.name!r}",
                    f.pos,
                )
            r = self._resolve_annot(f.ret_type)  # type: ignore
            self.funcs[f.name] = FuncSig(
                name=f.name,
                arity=len(f.params),
                n_defaults=_count_defaults(f.defaults),
                pos=f.pos,
                ret_type=(r[0], r[1], r[2]) if r is not None else None,
                ret_list_tuple_types=(r[3] if r is not None and r[1] == "tuple" else None),
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

        # Synthesise __init__ for @dataclass classes that don't define one.
        for c in self.mod.classes:
            if getattr(c, "is_dataclass", False):
                has_init = any(m.name == "__init__" for m in c.methods)
                if not has_init:
                    params: list = ["self"]
                    defaults: list = [None]
                    body_stmts: list = []
                    for fname, _fannot, fvalue in c.class_vars:
                        params.append(fname)
                        _func_nm = (
                            fvalue.func if isinstance(getattr(fvalue, "func", None), str)
                            else getattr(getattr(fvalue, "func", None), "name", None)
                        ) if isinstance(fvalue, A.Call) else None
                        if isinstance(fvalue, A.Call) and _func_nm == "field":
                            defaults.append(A.IntLit(value=0, pos=c.pos))
                        elif fvalue is not None:
                            defaults.append(fvalue)
                        else:
                            defaults.append(None)
                        body_stmts.append(
                            A.AttrAssign(
                                obj=A.Name(name="self", pos=c.pos),
                                name=fname,
                                value=A.Name(name=fname, pos=c.pos),
                                pos=c.pos,
                            )
                        )
                    init_func = A.FuncDef(
                        name="__init__",
                        params=params,
                        body=body_stmts,
                        defaults=defaults,
                        pos=c.pos,
                    )
                    c.methods.insert(0, init_func)

        # Collect class signatures so methods + constructor calls resolve.
        for c in self.mod.classes:
            if c.name in self.classes or c.name in self.funcs or c.name in BUILTINS:
                raise SemaError(
                    f"class name {c.name!r} collides with existing name", c.pos
                )
            sig = ClassSig(name=c.name, parent=c.parent, pos=c.pos)
            for m in c.methods:
                deco = getattr(m, "decorators", [])
                is_static = "staticmethod" in deco
                is_classm = "classmethod" in deco
                if not (is_static or is_classm):
                    if not m.params or m.params[0] != "self":
                        raise SemaError(
                            f"method {c.name}.{m.name!r} must take 'self' as its first parameter",
                            m.pos,
                        )
                mr = self._resolve_annot(m.ret_type)  # type: ignore
                # `@x.setter` methods are registered under a mangled name
                # ("x__setter") so they don't collide in `methods`/codegen
                # symbols with the `@property` getter of the same name "x".
                # `ClassSig.setters` maps the property name to this mangled
                # name so `obj.x = v` can be rewritten to dispatch to it.
                setter_prop = None
                for d in getattr(m, "decorators", []):
                    if d == f"{m.name}.setter":
                        setter_prop = m.name
                        break
                if setter_prop is not None:
                    m.name = f"{setter_prop}__setter"
                sig.methods[m.name] = FuncSig(
                    name=m.name,
                    arity=len(m.params),
                    n_defaults=_count_defaults(m.defaults),
                    pos=m.pos,
                    ret_type=(mr[0], mr[1], mr[2]) if mr is not None else None,
                    ret_list_tuple_types=(mr[3] if mr is not None and mr[1] == "tuple" else None),
                    param_names=list(m.params),
                    param_defaults=list(m.defaults),
                    vararg=m.vararg,
                    ret_tuple=self._scan_tuple_return(m.body),
                    decorators=list(getattr(m, "decorators", [])),
                    returns_self=mr is None and self._method_returns_self(m.body),
                )
                if setter_prop is not None:
                    sig.setters[setter_prop] = m.name
            self.classes[c.name] = sig

        # Validate parents and check for cycles. A parent that isn't a
        # user-defined class is treated as an *external* base — a builtin
        # (e.g. `Exception`) or a name imported from another module
        # (e.g. `Codegen` via `from .codegen import Codegen`). asmpython doesn't
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
        # Infer parameter types for unannotated, no-default parameters from
        # literal-typed arguments at call sites (see
        # `_infer_unannotated_params`). Done before field-type collection so
        # `self.x = param` in `__init__` benefits too.
        self._infer_unannotated_params()

        # Infer return types for functions/methods with no return annotation
        # and no inferred tuple-return shape, from their `return` statements
        # (using the parameter types just inferred above). See
        # `_infer_unannotated_returns`.
        self._infer_unannotated_returns()

        # Infer instance-field types from `self.x = ...` so `obj.x` reads carry
        # the right static type. Done before any body is checked (top-level or
        # method) so every field read — including from module-level code — sees
        # the inferred field types.
        self._collect_field_types()

        self.global_scope = Scope()
        # Module dunders the runtime always provides.
        self.global_scope.add("__name__", "str")
        self.global_scope.add("__file__", "str")
        self._check_block(self.mod.body, self.global_scope)

        # Function bodies: each has its own scope, seeded with globals then
        # params. If a param has a default literal, infer its type from the
        # default (so `def greet(p="hi")` makes p a str in the body).
        for f in self.mod.funcs:
            # `@assembly_func` bodies are raw NASM, not asmpython statements —
            # there's nothing for sema to check, and the docstring "body" would
            # otherwise read as a stray string ExprStmt. The signature was
            # already registered above from the annotations.
            if f.asm_body is not None:
                continue
            self.in_function = f.name
            self.in_lifted = getattr(f, "is_lifted", False)
            scope = Scope()
            self._seed_globals_into(scope)
            for i, p in enumerate(f.params):
                annot = f.param_types[i] if i < len(f.param_types) else None
                default = f.defaults[i] if i < len(f.defaults) else None
                inferred = self.inferred_param_types.get((f.name, i))
                self._seed_param(scope, p, annot, default, inferred)
            self._check_block(f.body, scope)
            self.in_function = None
            self.in_lifted = False

        # Method bodies: `self` is typed as the instance of its class.
        for c in self.mod.classes:
            for m in c.methods:
                if m.asm_body is not None:
                    continue  # raw-NASM method body, nothing to check
                self.in_function = f"{c.name}__{m.name}"
                self.current_class = c.name
                scope = Scope()
                self._seed_globals_into(scope)
                mdeco = getattr(m, "decorators", [])
                if "staticmethod" in mdeco:
                    # No implicit receiver: every parameter is a real argument.
                    start = 0
                elif "classmethod" in mdeco:
                    # First param is `cls` (opaque — asmpython has no class objs).
                    if m.params:
                        scope.add(m.params[0], "any")
                    start = 1
                else:
                    scope.add("self", f"instance:{c.name}")
                    start = 1
                for i, p in enumerate(m.params[start:], start=start):
                    annot = m.param_types[i] if i < len(m.param_types) else None
                    default = m.defaults[i] if i < len(m.defaults) else None
                    if (
                        i == 1
                        and annot is None
                        and default is None
                        and m.name in DUNDER_SAME_TYPE_OTHER
                    ):
                        # `other` on a binop/comparison dunder: assume it's
                        # another instance of this class (see
                        # DUNDER_SAME_TYPE_OTHER) so `other.field` resolves.
                        scope.add(p, f"instance:{c.name}")
                        continue
                    inferred = self.inferred_param_types.get((f"{c.name}.{m.name}", i))
                    self._seed_param(scope, p, annot, default, inferred)
                self._check_block(m.body, scope)
                self.in_function = None
                self.current_class = None

        # Hand resolved tables to codegen via the Module.
        self.mod.imported_modules = self.imported_modules
        self.mod.ffi_funcs = self.ffi_funcs
        self.mod.ffi_consts = self.ffi_consts
        # Codegen needs to look up methods by class chain for dispatch.
        self.mod.classes_sig = self.classes
        # Assembly packages pulled in via include(), in load order.
        self.mod.asm_packages = list(self.asm_packages.values())
        # Drop the `include(...)` directive statements from the module body so
        # codegen never tries to emit a runtime call for them. They were fully
        # consumed above (package loaded, exports registered).
        self.mod.body = [s for s in self.mod.body if not self._is_include_stmt(s)]

    # ---- helpers ------------------------------------------------------------

    def _check_block(self, stmts: list, scope: Scope) -> None:
        # Index-based so `_check_stmt` can splice extra statements (already
        # checked) immediately before `s` -- used by the `match` rewrite to
        # introduce a subject temp-variable assignment ahead of the `if`
        # chain it rewrites `s` into.
        i = 0
        while i < len(stmts):
            s = stmts[i]
            extra = self._check_stmt(s, scope)
            if extra:
                stmts[i:i] = extra
                i += len(extra)
            i += 1

    def _is_include_stmt(self, s) -> bool:
        """True if `s` is an `include("pkg")` directive statement.

        Matched structurally on the ExprStmt/Call shape; `include` having been
        bound as an asmdirective was already verified when it was handled, so
        any leftover `include(...)` ExprStmt in the module body is a directive.
        """
        return (
            isinstance(s, A.ExprStmt)
            and isinstance(s.expr, A.Call)
            and s.expr.func == "include"
        )

    def _handle_include(self, call: A.Call) -> None:
        """Resolve and load an `include("pkg")` directive.

        Validates the argument is a single string literal, finds the matching
        `<pkg>.asmpkg` on the search path, loads it, registers each export as a
        callable FFI-style symbol, and records the package on the Module so
        codegen can emit its NASM. Duplicate includes are ignored.
        """
        from asmpython.stdlib.assembly import pkgformat

        if len(call.args) != 1 or call.kwargs:
            raise SemaError("include() takes exactly one package-name string", call.pos)
        arg = call.args[0]
        if not isinstance(arg, A.StrLit):
            raise SemaError("include() argument must be a string literal", call.pos)
        name = arg.value
        if name in self.asm_packages:
            return  # already included

        # Search path: the source file's directory, then the CWD as a fallback.
        # Plain string directory paths (not pathlib) so this stays in the
        # compilable subset for self-host; pkgformat resolves them with string
        # ops + the os file-I/O FFI.
        search: list = []
        if self.source_dir is not None:
            search.append(str(self.source_dir))
        search.append(".")
        try:
            pkg_path = pkgformat.find_package(name, search)
            pkg = pkgformat.load_package(pkg_path)
        except pkgformat.AsmPkgError as e:
            raise SemaError(str(e), call.pos) from e

        self.asm_packages[name] = pkg
        # Register exports so call sites resolve. We reuse the FFI Func surface:
        # an exported symbol is just a foreign function with a known signature.
        for exp in pkg.exports.values():
            self.ffi_funcs[exp.symbol] = stdlib.Func(
                arg_types=tuple(exp.arg_types),
                ret_type=exp.ret_type,
                c_name=exp.symbol,
            )

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

    def _test_narrow_spec(self, test) -> Optional[tuple]:
        """The narrowing implied by a boolean condition: a bare
        `isinstance(x, T)` or the leading conjunct of an `and` chain.
        Returns a `(name, narrowed_type)` tuple, or None."""
        spec = self._isinstance_narrow_spec(test)
        if spec is not None:
            return spec
        if isinstance(test, A.BoolOp) and test.op == "and":
            return self._test_narrow_spec(test.left)
        return None

    def _apply_narrow(self, spec: tuple, scope: Scope):
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

    def _bind_comprehension_targets(self, e, el: str, child: "Scope") -> None:
        """Bind the loop variable(s) of a list/dict/set comprehension into
        `child`. Mirrors the `A.For` multi-target unpack handling: when
        `e.targets` is set (`for a, b in ...`), each flattened name is bound
        from the iterable's per-slot tuple shape if known, otherwise
        leniently as "any". Single-target comprehensions just bind `e.var`
        to `el`."""
        if not e.targets:
            if el == "tuple":
                child.add(e.var, el, tuple_types=self._tuple_elem_types(e.iter, child))
            else:
                child.add(e.var, el)
            return
        shape = list(getattr(e.iter, "tuple_elem_types", []) or [])
        flat = all(isinstance(t, str) for t in e.targets)
        for ti, nm in enumerate(self._flat_target_names(e.targets)):
            if flat and ti < len(shape) and shape[ti] not in ("int", "any"):
                child.add(nm, shape[ti])
            else:
                child.add(nm, "any")

    def _merge_walrus_bindings(self, scope: Scope, child: Scope, exclude: set) -> None:
        """Copy any *new* name bindings made in `child` (a comprehension's
        child scope) back into `scope`, except for the comprehension's own
        loop variable(s). Implements PEP 572: a `target := value` inside a
        comprehension binds `target` in the containing scope, not the
        comprehension's."""
        for name, ty in child.types.items():
            if name in exclude or name in scope.types:
                continue
            scope.types[name] = ty
            scope.bool_flags[name] = child.bool_flags.get(name, False)
            scope.none_flags[name] = child.none_flags.get(name, False)
            if name in child.list_el_types:
                scope.list_el_types[name] = child.list_el_types[name]
            if name in child.list_el_value_types:
                scope.list_el_value_types[name] = child.list_el_value_types[name]
            if name in child.list_el_tuple_types:
                scope.list_el_tuple_types[name] = child.list_el_tuple_types[name]
            if name in child.dict_value_types:
                scope.dict_value_types[name] = child.dict_value_types[name]
            if name in child.dict_inner_value_types:
                scope.dict_inner_value_types[name] = child.dict_inner_value_types[name]
            if name in child.dict_value_tuple_types:
                scope.dict_value_tuple_types[name] = child.dict_value_tuple_types[name]
            if name in child.tuple_elem_types:
                scope.tuple_elem_types[name] = child.tuple_elem_types[name]

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
            return ets[0] if ets and _all_same(ets) else "int"
        if t == "any":
            return "any"
        return "int"

    def _list_el_value_type(self, e, scope: Scope) -> str:
        """For a list whose elements are themselves containers (list[dict] /
        list[list]), the common value/element kind of those nested containers.
        'int' if unknown. Mirrors `_dict_inner_value_type` for lists."""
        if isinstance(e, A.ListLit):
            return getattr(e, "el_value_type", "int")
        if isinstance(e, A.Comprehension):
            return getattr(e, "el_value_type", "int")
        if isinstance(e, A.Name):
            return scope.list_el_value_types.get(e.name, "int")
        return "int"

    def _list_el_tuple_types(self, e, scope: Scope) -> list[str]:
        """For a list whose elements are tuples (list[tuple]), the common
        per-slot kinds of those tuples. [] if unknown."""
        if isinstance(e, A.ListLit):
            return list(getattr(e, "el_tuple_types", []))
        if isinstance(e, A.Comprehension):
            return list(getattr(e, "el_tuple_types", []))
        if isinstance(e, A.Name):
            return list(scope.list_el_tuple_types.get(e.name, []))
        if isinstance(e, A.MethodCall):
            # dict.items() -> list[(str, V)]; sema stamps the pair shape on
            # `tuple_elem_types` (the element tuple's per-slot kinds).
            return list(getattr(e, "tuple_elem_types", []))
        if isinstance(e, A.Call):
            # A user function annotated `-> list[tuple[T1,T2]]` stamps the
            # per-slot kinds onto the call node (see _check_call).
            return list(getattr(e, "tuple_elem_types", []))
        return []

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
        if isinstance(e, (A.DictLit, A.DictComprehension)):
            return getattr(e, "value_type", "int")
        if isinstance(e, A.Name):
            return scope.dict_value_types.get(e.name, "int")
        if isinstance(e, A.Attr):
            return getattr(e, "value_type", "int")
        if isinstance(e, (A.Call, A.MethodCall)):
            # A function / method annotated `-> dict[.., V]` stamps the value
            # kind onto the call node (sema fills it from the callee's sig).
            return getattr(e, "value_type", "int")
        if isinstance(e, A.Subscript):
            # A dict read out of an outer container: sema stamped "any" for the
            # untracked inner value kind.
            return getattr(e, "value_type", "int")
        if isinstance(e, A.BinOp) and e.op == "|":
            # `d1 | d2`: the merged dict's value kind, preferring whichever
            # side has a known (non-default) value type.
            lvt = self._dict_value_type(e.left, scope)
            return lvt if lvt != "int" else self._dict_value_type(e.right, scope)
        return "int"

    def _dict_inner_value_type(self, e, scope: Scope) -> str:
        """Inner value/element kind of a dict whose values are themselves
        containers (one nesting level). 'int' if unknown."""
        if isinstance(e, A.DictLit):
            return getattr(e, "inner_value_type", "int")
        if isinstance(e, A.Name):
            return scope.dict_inner_value_types.get(e.name, "int")
        return "int"

    def _dict_value_tuple_types(self, e, scope: Scope) -> list[str]:
        """Per-slot element kinds of a dict's values, when the value kind is
        itself a tuple (e.g. `dict[str, tuple[str, str]]` -> ["str", "str"]).
        [] if unknown / not a tuple-valued dict."""
        if isinstance(e, A.DictLit):
            return list(getattr(e, "value_tuple_elem_types", []))
        if isinstance(e, A.Name):
            return list(scope.dict_value_tuple_types.get(e.name, []))
        return []

    def _common_container_inner(self, values: list, scope: Scope) -> str:
        """Common inner value/element kind across a list of nested containers
        (the values of a dict whose value kind is 'dict' or 'list'). Returns
        the shared kind, or 'any' if they disagree, or 'int' if none is known.
        Used to type a chained `outer[k][k2]` read one level deep."""
        seen: str | None = None
        for v in values:
            vt = A.expr_type(v)
            if vt == "dict":
                inner = self._dict_value_type(v, scope)
            elif vt == "list":
                inner = self._list_el_type(v, scope)
            else:
                continue  # opaque/other: no inner kind to contribute
            if seen is None:
                seen = inner
            elif seen != inner:
                seen = "any"
        return seen if seen is not None else "int"

    def _common_tuple_slots(self, values: list, scope: Scope) -> list[str]:
        """Common per-slot kinds across a list of tuple expressions. Returns the
        shared slot kinds, with a slot set to 'any' where the tuples disagree,
        or [] if shapes differ / none are known. Used to type list[tuple]."""
        shared: list[str] | None = None
        for v in values:
            slots = self._tuple_elem_types(v, scope)
            if not slots:
                return []
            if shared is None:
                shared = list(slots)
            elif len(shared) != len(slots):
                return []  # ragged shapes: give up
            else:
                merged: list[str] = []
                i = 0
                for s in shared:
                    merged.append(s if s == slots[i] else "any")
                    i += 1
                shared = merged
        return shared if shared is not None else []

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
            # Distinct kinds per slot as a dedup list (not a set + .pop(): a
            # genexpr-in-set and arbitrary set.pop are outside the compilable
            # subset — same idiom as the tuple-membership check).
            kinds: list = []
            for sh in same:
                if sh[i] not in kinds:
                    kinds.append(sh[i])
            merged.append(kinds[0] if len(kinds) == 1 else "any")
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

    def _method_returns_self(self, stmts: list) -> bool:
        """True if every reachable `return` in `stmts` is `return self`, and
        at least one such return exists. Mirrors `_scan_tuple_return`'s
        body-scanning approach (see `FuncSig.returns_self`)."""
        returns: list = []
        self._collect_returns(stmts, returns)
        if not returns:
            return False
        return all(
            isinstance(r.value, A.Name) and r.value.name == "self" for r in returns
        )

    def _collect_returns(self, stmts: list, acc: list) -> None:
        for s in stmts:
            if isinstance(s, A.Return):
                acc.append(s)
            elif isinstance(s, A.If):
                self._collect_returns(s.then, acc)
                self._collect_returns(s.orelse, acc)
            elif isinstance(s, (A.While, A.For)):
                self._collect_returns(s.body, acc)
            elif isinstance(s, A.Try):
                self._collect_returns(s.body, acc)
                self._collect_returns(s.handler, acc)
                for _types, _bind, hbody in s.extra_handlers:
                    self._collect_returns(hbody, acc)
                self._collect_returns(s.else_body, acc)
                self._collect_returns(s.finally_body, acc)

    def _bind_name_from_value(self, target: str, value, scope: Scope, annot=None) -> None:
        """Bind `target` in `scope` to the static type of `value`, the same
        way a plain `target = value` assignment would. Shared by `A.Assign`
        and `A.NamedExpr` (the walrus operator `target := value`)."""
        # Remember a name bound directly to a lambda, so a later `name(...)`
        # call recovers the lambda's result type instead of defaulting int.
        if isinstance(value, A.Lambda):
            self.lambda_rets[target] = getattr(value, "lambda_ret", "int")
        t = A.expr_type(value)
        # A declaration annotation (`name: T = value`) overrides inference
        # when it constrains the type — this is how `xs: list[str] = []`
        # pins the element kind even though the empty initializer infers
        # nothing. Honor it only when the inferred type is the unknown
        # default ("int") or the annotation refines a same-kind container.
        ann = self._resolve_annot(annot)
        if ann is not None:
            aty, ael, aval, atup, aelval = ann
            if t in ("int", "any") or t == aty:
                if aty == "list":
                    scope.add(
                        target,
                        "list",
                        el_type=ael or self._list_el_type(value, scope),
                        el_value_type=aelval,
                    )
                    return
                if aty == "dict":
                    scope.add(
                        target,
                        "dict",
                        value_type=aval or self._dict_value_type(value, scope),
                        inner_value_type=self._dict_inner_value_type(value, scope),
                        value_tuple_types=self._dict_value_tuple_types(value, scope),
                    )
                    return
                if aty == "tuple":
                    scope.add(
                        target,
                        "tuple",
                        tuple_types=atup or self._tuple_elem_types(value, scope),
                    )
                    return
                if aty in ("str", "float", "any") or aty.startswith("instance:"):
                    scope.add(target, aty)
                    return
        if t == "list":
            scope.add(
                target,
                t,
                el_type=self._list_el_type(value, scope),
                el_value_type=self._list_el_value_type(value, scope),
                el_tuple_types=self._list_el_tuple_types(value, scope),
            )
        elif t == "dict":
            scope.add(
                target,
                t,
                value_type=self._dict_value_type(value, scope),
                inner_value_type=self._dict_inner_value_type(value, scope),
                value_tuple_types=self._dict_value_tuple_types(value, scope),
            )
        elif t == "tuple":
            scope.add(target, t, tuple_types=self._tuple_elem_types(value, scope))
        else:
            scope.add(
                target,
                t,
                is_bool=t == "int" and A.is_bool_expr(value),
                is_none=t == "int" and A.is_none_expr(value),
            )

    def _check_stmt(self, s, scope: Scope) -> "Optional[list]":
        if isinstance(s, A.Pass):
            return
        if isinstance(s, A.Assign):
            self._check_expr(s.value, scope)
            self._bind_name_from_value(
                s.target, s.value, scope, getattr(s, "annot", None)
            )
            return
        if isinstance(s, A.TupleAssign):
            # Resolve the RHS first so tuple-returning calls have their type
            # set before we decide between unpack and parallel forms.
            for v in s.values:
                self._check_expr(v, scope)
            star_targets = [t for t in s.targets if isinstance(t, A.StarTarget)]
            if star_targets:
                # `a, *rest = xs` / `*init, last = xs` / `a, *mid, b = xs`
                # (PEP 3132). Only the single-iterable unpack form of a
                # `list`-typed RHS is supported: `rest`/`init`/`mid` becomes a
                # fresh list of the same element kind, holding whichever
                # elements aren't claimed by the plain targets on either side
                # of the star.
                if len(s.values) != 1:
                    raise SemaError(
                        "starred assignment requires a single list on the "
                        "right-hand side",
                        s.pos,
                    )
                rhs_t = A.expr_type(s.values[0])
                if rhs_t != "list":
                    raise SemaError(
                        f"starred assignment requires a list on the "
                        f"right-hand side, got {rhs_t}",
                        s.pos,
                    )
                el = self._list_el_type(s.values[0], scope)
                el_bound = el if el != "int" else "any"
                for t in s.targets:
                    if isinstance(t, A.StarTarget):
                        scope.add(t.name, "list", el_type=el)
                    else:
                        scope.add(t.name, el_bound)
                return
            nonname_targets = [t for t in s.targets if not isinstance(t, A.Name)]
            if nonname_targets and len(s.values) == 1:
                # The unpack forms below (`a, b = <tuple/list/...>`) only know
                # how to bind plain names; subscript/attribute targets need
                # one value per target.
                raise SemaError(
                    "tuple assign with subscript/attribute targets requires "
                    "the parallel form (one value per target)",
                    s.pos,
                )
            # Unpack form: `a, b = <single tuple expr>` (a literal, a tuple
            # variable, or a call to a tuple-returning function).
            if len(s.values) == 1 and A.expr_type(s.values[0]) == "tuple":
                ets = self._tuple_elem_types(s.values[0], scope)
                if ets and len(ets) != len(s.targets):
                    raise SemaError(
                        f"cannot unpack {len(ets)}-tuple into {len(s.targets)} target(s)",
                        s.pos,
                    )
                # Bind each target from the tuple's per-slot kind. A missing
                # slot, or an "int" slot (asmpython's unknown sentinel — a slot
                # holding an inferred-but-untracked object, e.g. a FuncSig
                # pulled from a dict), binds opaque so `target.attr` stays
                # lenient. A concrete scalar/instance slot keeps its kind.
                for i, t in enumerate(s.targets):
                    slot = ets[i] if i < len(ets) else "any"
                    scope.add(t.name, "any" if slot == "int" else slot)
                return
            if len(s.values) == 1 and A.expr_type(s.values[0]) in (
                "any",
                "int",
                "list",
                "str",
            ):
                # Unpacking a single iterable / opaque value into N targets:
                #   a, b = some_list            (e.g. str.split("->", 1))
                #   a, b = opaque_pair          (tuple read from an untyped dict)
                # "int" doubles as asmpython's unknown sentinel. The runtime
                # unpacks the iterable's slots into the targets (see codegen's
                # TupleAssign unpack form). Bind every target leniently rather
                # than mis-reading it as a parallel-arity mismatch. The genuine
                # parallel-arity error (`a, b = 1, 2, 3`) still fires below: it
                # has len(values) > 1.
                el = "any"
                if A.expr_type(s.values[0]) == "list":
                    el = self._list_el_type(s.values[0], scope)
                    el = el if el != "int" else "any"
                elif A.expr_type(s.values[0]) == "str":
                    el = "str"
                for t in s.targets:
                    scope.add(t.name, el)
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
                        f"tuple assign target: float values aren't supported in "
                        "parallel assignment yet (assign separately)",
                        s.pos,
                    )
                if vt not in (
                    "int",
                    "str",
                    "any",
                    "list",
                    "dict",
                    "tuple",
                    "set",
                ) and not vt.startswith("instance:"):
                    raise SemaError(
                        f"tuple assign target: unsupported value type {vt}",
                        s.pos,
                    )
                self._check_tuple_assign_target(t, vt, scope, s.pos)
            return
        if isinstance(s, A.AugAssign):
            if s.target not in scope.names:
                raise SemaError(
                    f"augmented assignment to undefined variable {s.target!r}",
                    s.pos,
                )
            self._check_expr(s.value, scope)
            # `d |= other` (PEP 584): in-place dict union, merging `other`'s
            # entries into `d` (overwriting on key conflicts).
            if s.op == "|" and scope.types.get(s.target) == "dict":
                rt = A.expr_type(s.value)
                if rt not in ("dict", "any"):
                    raise SemaError(
                        f"unsupported operand type for |=: dict |= {rt}", s.pos
                    )
                return
            # `b += 1` etc. demotes a tracked bool back to a plain int, as in
            # CPython (bool has no augmented-assign dunders of its own).
            scope.bool_flags[s.target] = False
            scope.none_flags[s.target] = False
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
            # `x.attr` reads resolve (the dispatch pattern throughout asmpython).
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
            self._check_block(getattr(s, "orelse", []), scope)
            return
        if isinstance(s, A.For):
            # zip(A, B) / enumerate(zip(A, B)): parallel iteration with an
            # optional index. Recognized before the plain-enumerate handler.
            zspec = self._for_zip_spec(s)
            if zspec is not None:
                idx_name, a_name, b_name, a_expr, b_expr = zspec
                self._check_expr(a_expr, scope)
                self._check_expr(b_expr, scope)
                # zip operands must be iterable: lists or tuples (which share the
                # list layout) — or opaque, which we trust leniently.
                if A.expr_type(a_expr) not in ("list", "tuple", "any") or A.expr_type(
                    b_expr
                ) not in ("list", "tuple", "any"):
                    raise SemaError("zip() arguments must be lists or tuples", s.pos)
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
                if len(s.iter.args) not in (1, 2):
                    raise SemaError(
                        "enumerate() takes 1 or 2 arguments", s.pos
                    )
                if len(s.targets) != 2:
                    raise SemaError(
                        "for ... in enumerate(...) needs two targets "
                        "(`for i, x in enumerate(xs)`)",
                        s.pos,
                    )
                inner = s.iter.args[0]
                self._check_expr(inner, scope)
                if len(s.iter.args) == 2:
                    start_arg = s.iter.args[1]
                    self._check_expr(start_arg, scope)
                    if A.expr_type(start_arg) != "int":
                        raise SemaError(
                            "enumerate() start argument must be an int",
                            s.pos,
                        )
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
                # Multi-target unpack (`for a, b in <iterable-of-pairs>`): each
                # element is itself a tuple/pair whose per-slot kinds we don't
                # track, so bind every target leniently. Handles list-of-tuples
                # and tuple-of-tuples uniformly. (zip/enumerate were already
                # handled above with precise element kinds.)
                if s.targets and it_t in ("list", "tuple", "dict", "str", "any", "int"):
                    # `for a, b in <list[T]>` where each element is a plain
                    # user-class instance (T not itself a tuple/list/dict)
                    # has no list/tuple buffer to unpack: codegen's
                    # _gen_for_list would dereference the instance pointer as
                    # if it were a list header and segfault. CPython rejects
                    # this at runtime with "cannot unpack non-iterable X
                    # object"; reject it at compile time instead.
                    if it_t == "list":
                        el_t = self._list_el_type(s.iter, scope)
                        if el_t.startswith("instance:"):
                            cls = el_t.split(":", 1)[1]
                            raise SemaError(
                                f"cannot unpack non-iterable {cls} object",
                                s.pos,
                            )
                    # If the iterable carries a per-pair slot shape and the
                    # targets are FLAT names (a nested group like
                    # `for k, (a, b) in ...` consumes one slot per group, so slot
                    # kinds don't map 1:1 onto the flattened names), type each
                    # target from its slot; otherwise bind leniently.
                    #   - list[tuple]: element tuples' per-slot kinds
                    #   - direct tuple shape (d.items(), tuple-of-tuples)
                    if it_t == "list":
                        shape = self._list_el_tuple_types(s.iter, scope)
                    else:
                        shape = list(getattr(s.iter, "tuple_elem_types", []) or [])
                    flat = True
                    for t in s.targets:
                        if not isinstance(t, str):
                            flat = False
                    names = self._flat_target_names(s.targets)
                    ttypes: list[str] = []
                    for ti, nm in enumerate(names):
                        # Only bind a target to a concrete kind when the slot is
                        # str/float — the kinds that misprint as int. "int"/"any"
                        # stay lenient (the historical behavior the self-host
                        # build relies on, and which keeps append targets open).
                        # Append only string literals so this stays self-host
                        # compilable (a subscript result types as int otherwise).
                        is_str = flat and ti < len(shape) and shape[ti] == "str"
                        is_flt = flat and ti < len(shape) and shape[ti] == "float"
                        if is_str:
                            scope.add(nm, "str")
                            ttypes.append("str")
                        elif is_flt:
                            scope.add(nm, "float")
                            ttypes.append("float")
                        else:
                            scope.add(nm, "any")
                            ttypes.append("any")
                    if flat:
                        s.target_types = ttypes  # codegen local typing
                    self.loop_depth += 1
                    try:
                        self._check_block(s.body, scope)
                    finally:
                        self.loop_depth -= 1
                    return
                if it_t == "list":
                    el_t = self._list_el_type(s.iter, scope)
                    if el_t == "tuple":
                        # Single-var iteration over list[tuple] (`for pair in xs`).
                        # Multi-target unpack is handled by the branch above and
                        # returns before reaching here. Carry the per-slot kinds
                        # so `pair[0]` types correctly.
                        slots = self._list_el_tuple_types(s.iter, scope)
                        scope.add(s.var, el_t, tuple_types=slots)
                    elif el_t == "dict":
                        # list[dict]: bind the loop var as a dict carrying the
                        # tracked value kind so `x[k]` recovers the leaf type.
                        inner = self._list_el_value_type(s.iter, scope)
                        scope.add(
                            s.var, "dict", value_type=inner if inner != "int" else "any"
                        )
                    elif el_t == "list":
                        # list[list]: bind as a list carrying the inner element
                        # kind so `x[i]` recovers the leaf type.
                        inner = self._list_el_value_type(s.iter, scope)
                        scope.add(
                            s.var, "list", el_type=inner if inner != "int" else "any"
                        )
                    else:
                        scope.add(s.var, el_t)
                elif it_t == "tuple":
                    # Iterating a tuple needs a single element type, so only
                    # homogeneous tuples may be iterated; index heterogeneous
                    # ones instead.
                    ets = self._tuple_elem_types(s.iter, scope)
                    if not ets:
                        scope.add(s.var, "int")
                    elif _all_same(ets):
                        scope.add(s.var, ets[0])
                    else:
                        raise SemaError(
                            "cannot iterate a heterogeneous tuple; index its elements instead",
                            s.pos,
                        )
                elif it_t == "dict":
                    # Iterating a dict yields its keys (strings).
                    scope.add(s.var, "str")
                elif it_t in ("set", "frozenset"):
                    # Iterating a set yields its elements (opaque — could be str
                    # or int, but we default to str which is the common case).
                    scope.add(s.var, "str")
                elif it_t == "str":
                    # Each iteration yields a fresh 1-char str.
                    scope.add(s.var, "str")
                elif it_t in ("any", "int"):
                    # Opaque iterable (e.g. a value read out of an unannotated
                    # container, or an unannotated field typed "int" by default):
                    # bind every target leniently.
                    if s.targets:
                        for nm in self._flat_target_names(s.targets):
                            scope.add(nm, "any")
                    else:
                        scope.add(s.var, "any")
                else:
                    raise SemaError(
                        "asmpython 'for' iterates over range(), list, dict, tuple, or str",
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
            self._check_block(getattr(s, "orelse", []), scope)
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
                # Module isn't in asmpython's stdlib registry — accept the
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
            # `from asmpython.stdlib import os` (the compiler imports its stdlib
            # by full path to dodge CPython's stdlib at compile time): bind each
            # imported name that names a stdlib module as an FFI module, so
            # `os.fopen(...)` dispatches through BINDINGS just like a bare
            # `import os`. Names that aren't stdlib modules fall through to the
            # generic handling below.
            if s.level == 0 and (
                s.module in ("asmpython.stdlib", "asmpython._stdlib")
                or s.module.startswith("asmpython.stdlib.")
                or s.module.startswith("asmpython._stdlib.")
            ):
                for name, orig in zip(s.names, s.orig_names or s.names):
                    try:
                        self.imported_modules[name] = _load_module(orig)
                        scope.add(name, "module")
                    except SemaError:
                        # A stdlib *submodule* that isn't an FFI binding set
                        # (e.g. `ospath`, `assembly.pkgformat`). Bind it as a
                        # module so `name.func(...)` dispatches to the merged
                        # project function (whole-program) — unless the name was
                        # already bound (e.g. a materialized value global like
                        # `BINDINGS`): re-binding would clobber its real type.
                        # Uppercase names are constants/classes, not submodules.
                        if name not in scope:
                            ty = "any" if orig[:1].isupper() else "module"
                            scope.add(name, ty)
                return
            # `from asmpython.assembly import assembly_func, include`: the two
            # compiler directives. Bind them so call sites resolve; `include`
            # is acted on when called, `assembly_func` is consumed at parse time
            # as a decorator (its name in scope is just a marker here).
            if s.module in ("asmpython.assembly", "assembly") and s.level == 0:
                for name in s.names:
                    # Only the two directives are special; anything else from
                    # the package (e.g. `pkgformat`) is an ordinary opaque name.
                    if name in ("assembly_func", "include"):
                        scope.add(name, "asmdirective")
                    else:
                        scope.add(name, "any")
                return
            # Relative import or unknown module: accept the syntax and bind
            # each imported name as a dummy int. Self-host needs every source
            # file to *parse*; real cross-file resolution comes later.
            if s.level > 0 or not s.module:
                # `from . import ast_nodes as A` (no module name, just dots)
                # imports sibling *modules* — bind them as modules so
                # `A.Module(...)` / `A.expr_type(...)` stay lenient. A relative
                # *name* import (`from .x import Y`) binds an opaque value
                # ("any") so `Y(...)` / `Y.method()` / `Y.attr` all stay lenient
                # rather than erroring as operations on an int.
                bind_ty = "module" if not s.module else "any"
                for name in s.names:
                    # Don't clobber a name the whole-program loader already
                    # materialized with its real type (`from .._stdlib import
                    # STDLIB_BINDINGS` after the dict global was prepended).
                    if name not in scope:
                        scope.add(name, bind_ty)
                return
            try:
                bindings = _load_module(s.module)
            except SemaError:
                # Unknown absolute module (e.g. `from pathlib import Path`).
                # Bind each name as an opaque value so `Path(...)`, `Path.cwd()`,
                # and attribute access stay lenient (these are real CPython
                # imports the compiler uses but asmpython doesn't model).
                # Never clobber a name that's already bound — a merged project
                # import (`from asmpython._compiler.sema import STDLIB_BINDINGS`)
                # refers to a value the whole-program loader already
                # materialized with its REAL type.
                # For `from bundled_module import orig as local`, register the
                # alias so codegen can resolve `local` to the merged `orig` symbol.
                orig_names = s.orig_names or s.names
                for local, orig in zip(s.names, orig_names):
                    if local != orig:
                        self.mod.func_aliases[local] = orig
                    if local not in scope:
                        scope.add(local, "any")
                return
            for name in s.names:
                if name not in bindings:
                    # Unknown binding inside a known module — accept as an
                    # opaque value (mirrors the unknown-module fallback above).
                    scope.add(name, "any")
                    continue
                b = bindings[name]
                if isinstance(b, stdlib.Func):
                    self.ffi_funcs[name] = b
                else:
                    self.ffi_consts[name] = b
                    scope.add(name, b.ty)
            return
        if isinstance(s, A.ExprStmt):
            # `include("pkg")` — an assembly-package directive. Recognised only
            # when `include` came from `asmpython.assembly` (bound as
            # "asmdirective") so a user function named `include` still works.
            if (
                isinstance(s.expr, A.Call)
                and s.expr.func == "include"
                and scope.types.get("include") == "asmdirective"
            ):
                self._handle_include(s.expr)
                return
            self._check_expr(s.expr, scope)
            return
        if isinstance(s, A.IndexAssign):
            self._check_expr(s.target.obj, scope)
            self._check_expr(s.target.index, scope)
            obj_t = A.expr_type(s.target.obj)
            self._check_expr(s.value, scope)
            value_t = A.expr_type(s.value)
            # Slice assignments (a[x:y] = b) are always lenient — codegen
            # doesn't model slices, but sema must accept them.
            if isinstance(s.target.index, A.Slice):
                return
            if obj_t == "list":
                el_t = self._list_el_type(s.target.obj, scope)
                # "int" doubles as asmpython's unknown/default kind, so treat it
                # (like "?"/"any") as a wildcard rather than a hard mismatch —
                # asmpython's shallow inference types many str/instance values as
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
                # "int" doubles as asmpython's unknown sentinel (an untracked
                # element/slot that is a str at runtime), so it's lenient here.
                _ikt = A.expr_type(s.target.index)
                if _ikt not in ("str", "any", "int") and not _ikt.startswith("instance:"):
                    raise SemaError("dict keys must be strings", s.pos)
                dvt = self._dict_value_type(s.target.obj, scope)
                if (
                    dvt not in ("any", "int")
                    and value_t not in ("any", "int")
                    and value_t != dvt
                    # Both instance types: one may be a subtype of the other.
                    and not (dvt.startswith("instance:") and value_t.startswith("instance:"))
                ):
                    raise SemaError(
                        f"dict[k] = v: dict values are {dvt}, got {value_t}",
                        s.pos,
                    )
            elif obj_t == "any":
                pass  # opaque target: accept the index assignment leniently
            elif obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                cls_sig = self.classes.get(cls_name)
                msig = None
                if cls_sig is not None:
                    msig = cls_sig.methods.get("__setitem__")
                if msig is None:
                    raise SemaError(
                        f"'{cls_name}' object does not support index assignment", s.pos
                    )
                s.target._setitem_class = cls_name  # type: ignore[attr-defined]
            else:
                raise SemaError(f"cannot index a {obj_t}", s.pos)
            return
        if isinstance(s, A.AttrAssign):
            # Class-level variable write: `ClassName.x = v`. Allowed when the
            # class declares `x` as a (non-dataclass) class var.
            if (
                isinstance(s.obj, A.Name)
                and s.obj.name in self.classes
                and self._class_var_type(s.obj.name, s.name) is not None
            ):
                self._check_expr(s.value, scope)
                return
            self._check_expr(s.obj, scope)
            obj_t = A.expr_type(s.obj)
            if not obj_t.startswith("instance:") and obj_t not in ("any", "module", "int"):
                raise SemaError(
                    f"cannot assign attribute on {obj_t}",
                    s.pos,
                )
            if obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                if cls_name in self.classes:
                    resolved = self._resolve_method(cls_name, s.name)
                    if resolved is not None and "property" in resolved[1].decorators:
                        setter_name = self._resolve_setter(cls_name, s.name)
                        if setter_name is None:
                            # A read-only property can never be assigned,
                            # just like in CPython.
                            raise SemaError(
                                f"property {s.name!r} of {cls_name!r} object has no setter",
                                s.pos,
                            )
                        # `obj.x = value` -> `obj.x__setter(value)`: rewrite
                        # this AttrAssign into an ExprStmt wrapping a
                        # MethodCall, in place, so codegen's existing
                        # method-dispatch (incl. virtual dispatch) handles it.
                        obj_expr = s.obj
                        value_expr = s.value
                        s.__class__ = A.ExprStmt  # type: ignore[assignment]
                        s.expr = A.MethodCall(  # type: ignore[attr-defined]
                            obj=obj_expr,
                            method=setter_name,
                            args=[value_expr],
                            pos=s.pos,
                        )
                        self._check_expr(s.expr, scope)  # type: ignore[attr-defined]
                        return
            self._check_expr(s.value, scope)
            value_t = A.expr_type(s.value)
            # Instance fields hold any 8-byte value (int / str-ptr / instance /
            # list / dict / tuple / float bit pattern).
            user_instance = obj_t.startswith("instance:") and (
                obj_t.split(":", 1)[1] in self.classes
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
            for name in s.handler_types:
                self._check_exc_type_name(name, s.pos)
            # `except ... as e` binds the caught exception's message string
            # (asmpython's native exception payload). Codegen relies on this
            # being `str` so `print(e)` prints it correctly.
            if s.bind_name is not None:
                scope.add(s.bind_name, "str")
            self._check_block(s.handler, scope)
            for types, bind_name, hbody in s.extra_handlers:
                for name in types:
                    self._check_exc_type_name(name, s.pos)
                if bind_name is not None:
                    scope.add(bind_name, "str")
                self._check_block(hbody, scope)
            self._check_block(s.else_body, scope)
            self._check_block(s.finally_body, scope)
            return
        if isinstance(s, A.Raise):
            if s.value is None:
                # Bare `raise`: re-raises the currently-active exception.
                return
            self._check_expr(s.value, scope)
            vt = A.expr_type(s.value)
            # Accept a bare string message (asmpython's native exception payload),
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
        if isinstance(s, A.With):
            self._check_expr(s.expr, scope)
            obj_t = A.expr_type(s.expr)
            if obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                enter = self._resolve_method(cls_name, "__enter__")
                exitm = self._resolve_method(cls_name, "__exit__")
                if enter is None or exitm is None:
                    raise SemaError(
                        f"{cls_name!r} object does not support the context "
                        "manager protocol (missing __enter__/__exit__)",
                        s.pos,
                    )
                # `with expr as name: body` -> rewrite *in place* into:
                #   __cm = expr
                #   [name = ] __cm.__enter__()
                #   try:
                #       body
                #   finally:
                #       __cm.__exit__(None, None, None)
                # so the existing setjmp/longjmp try/finally machinery makes
                # __exit__ run even if `body` raises. asmpython's exception
                # model doesn't carry rich exception objects, so __exit__
                # always sees (None, None, None) -- it can't inspect or
                # suppress the exception, only run cleanup.
                cm_name = f"__cm_{id(s)}"
                cm_assign = A.Assign(target=cm_name, value=s.expr, pos=s.pos)
                cm_ref = A.Name(name=cm_name, pos=s.pos)
                enter_call = A.MethodCall(
                    obj=cm_ref, method="__enter__", args=[], pos=s.pos
                )
                if s.name is not None:
                    enter_stmt: A.Stmt = A.Assign(
                        target=s.name, value=enter_call, pos=s.pos
                    )
                else:
                    enter_stmt = A.ExprStmt(expr=enter_call, pos=s.pos)
                none_args = [
                    A.IntLit(value=0, pos=s.pos, is_none=True) for _ in range(3)
                ]
                exit_call = A.MethodCall(
                    obj=A.Name(name=cm_name, pos=s.pos),
                    method="__exit__",
                    args=none_args,
                    pos=s.pos,
                )
                body = [cm_assign, enter_stmt] + list(s.body)
                s.__class__ = A.Try  # type: ignore[assignment]
                s.body = body  # type: ignore[attr-defined]
                s.handler = []  # type: ignore[attr-defined]
                s.bind_name = None  # type: ignore[attr-defined]
                s.handler_types = []  # type: ignore[attr-defined]
                s.extra_handlers = []  # type: ignore[attr-defined]
                s.else_body = []  # type: ignore[attr-defined]
                s.finally_body = [A.ExprStmt(expr=exit_call, pos=s.pos)]  # type: ignore[attr-defined]
                self._check_stmt(s, scope)
                return
            if s.name is not None:
                scope.add(s.name, A.expr_type(s.expr))
            self._check_block(s.body, scope)
            return
        if isinstance(s, A.MultiAssign):
            self._check_expr(s.value, scope)
            vt = A.expr_type(s.value)
            for nm in s.targets:
                scope.add(nm, vt)
            return
        if isinstance(s, A.Global):
            # `global x, y`: just validates that the names exist at module level.
            # Codegen uses this to skip allocating frame slots for them.
            for nm in s.names:
                if nm not in scope and nm not in self.global_scope:
                    pass  # allow forward-declared globals (assigned before use)
            return
        if isinstance(s, A.Nonlocal):
            # `nonlocal x, y`: closures aren't supported; accept and ignore.
            return
        if isinstance(s, A.Del):
            # `del x` or `del x[k]`: type-check the target expression leniently.
            try:
                self._check_expr(s.target, scope)
            except Exception:
                pass
            return
        if isinstance(s, A.Match):
            # Rewrite `match subject: case p [if g]: body ...` in place into a
            # subject-temp assignment + an if/elif/.../else chain, then type-check
            # the resulting if-chain. The subject is evaluated exactly once.
            subj_name = f"__match_subj_{id(s)}"
            subj_assign = A.Assign(target=subj_name, value=s.subject, pos=s.pos)
            self._check_stmt(subj_assign, scope)

            # Collect per-case pre-stmts (elem temps that must be in scope before
            # each case's test), which are spliced in before the if-chain.
            all_pre: list = [subj_assign]

            orelse: list = []
            for pattern, guard, body in reversed(s.cases):
                pre, test, binds = self._lower_pattern(pattern, subj_name, s.pos)
                for p in pre:
                    self._check_stmt(p, scope)
                all_pre.extend(pre)
                if guard is not None:
                    # Put binds before the guard so captured names are in scope
                    # when the guard expression is evaluated, then nest the guard
                    # inside the pattern-match if-arm: `if test: binds; if guard: body; else: next`
                    inner_if = A.If(
                        test=guard, then=list(body), orelse=orelse, pos=s.pos
                    )
                    if_node = A.If(
                        test=test, then=binds + [inner_if], orelse=orelse, pos=s.pos
                    )
                else:
                    if_node = A.If(
                        test=test, then=binds + list(body), orelse=orelse, pos=s.pos
                    )
                orelse = [if_node]

            if not orelse:
                return all_pre[1:] or None

            top = orelse[0]
            s.__class__ = A.If  # type: ignore[assignment]
            s.test = top.test  # type: ignore[attr-defined]
            s.then = top.then  # type: ignore[attr-defined]
            s.orelse = top.orelse  # type: ignore[attr-defined]
            self._check_stmt(s, scope)
            return all_pre

        raise SemaError(
            f"internal: unhandled stmt {type(s).__name__}", getattr(s, "pos", None)
        )

    # ---- match/case helpers -------------------------------------------------

    def _make_name_ref(self, name: str, pos) -> A.Name:
        """Return a fresh Name node for `name`. Must build a new node every
        call — never reuse a node in two places in the rewritten AST tree."""
        return A.Name(name=name, pos=pos)

    def _and_chain(self, exprs: list, pos) -> A.Expr:
        """Left-fold a non-empty list of expressions with `and`."""
        result = exprs[0]
        for e in exprs[1:]:
            result = A.BoolOp(op="and", left=result, right=e, pos=pos)
        return result

    def _or_chain(self, exprs: list, pos) -> A.Expr:
        """Left-fold a non-empty list of expressions with `or`."""
        result = exprs[0]
        for e in exprs[1:]:
            result = A.BoolOp(op="or", left=result, right=e, pos=pos)
        return result

    def _lower_pattern(
        self, pattern, subj_name: str, pos
    ) -> tuple:
        """Lower one pattern into (pre_stmts, test_expr, bind_stmts).

        `subj_name` is the name of the synthetic subject temp variable.
        Every call to `_make_name_ref(subj_name, pos)` produces a fresh node.

        Returns:
            pre_stmts  — list[A.Assign] that must be executed BEFORE the test
                         (e.g. element-temp assignments for sequence sub-patterns).
                         These are hoisted to just before the enclosing if-node.
            test_expr  — an A.Expr that evaluates to truthy iff the pattern
                         matches the subject (may be a fresh IntLit(1) for
                         unconditional matches).
            bind_stmts — list[A.Assign] that bind captured names; placed in the
                         if-node's `then` body so they only run when the test passes.
        """
        _TRUE = A.IntLit(value=1, pos=pos, is_bool=True)

        if isinstance(pattern, A.MatchValue):
            test = A.Compare(
                ops=["=="],
                operands=[self._make_name_ref(subj_name, pos), pattern.value],
                pos=pattern.pos,
            )
            return [], test, []

        if isinstance(pattern, A.MatchCapture):
            if pattern.name == "_":
                return [], _TRUE, []
            bind = A.Assign(
                target=pattern.name,
                value=self._make_name_ref(subj_name, pos),
                pos=pattern.pos,
            )
            return [], _TRUE, [bind]

        if isinstance(pattern, A.MatchOr):
            for alt in pattern.patterns:
                if isinstance(alt, A.MatchCapture) and alt.name != "_":
                    raise SemaError(
                        "capture patterns are not allowed inside or-patterns",
                        alt.pos,
                    )
            tests = [
                self._lower_pattern(p, subj_name, pattern.pos)[1]
                for p in pattern.patterns
            ]
            return [], self._or_chain(tests, pattern.pos), []

        if isinstance(pattern, A.MatchSequence):
            seq_tests: list = []
            seq_binds: list = []
            seq_pre: list = []  # assigns that must happen BEFORE the test (elem temps)
            star_index = pattern.star_index
            n_fixed = len(pattern.patterns) - (1 if star_index is not None else 0)

            # Length check: exact when no star, >= n_fixed when starred.
            len_call = A.Call(
                func="len",
                args=[self._make_name_ref(subj_name, pos)],
                pos=pattern.pos,
            )
            if star_index is None:
                len_test = A.Compare(
                    ops=["=="],
                    operands=[len_call, A.IntLit(value=n_fixed, pos=pattern.pos)],
                    pos=pattern.pos,
                )
            else:
                len_test = A.Compare(
                    ops=[">="],
                    operands=[len_call, A.IntLit(value=n_fixed, pos=pattern.pos)],
                    pos=pattern.pos,
                )
            seq_tests.append(len_test)

            n_after = (len(pattern.patterns) - star_index - 1) if star_index is not None else 0

            for i, sub in enumerate(pattern.patterns):
                if star_index is not None and i == star_index:
                    # Star capture: bind subj[i : len(subj)-n_after].
                    if isinstance(sub, A.MatchCapture) and sub.name != "_":
                        if n_after == 0:
                            stop_node = None
                        else:
                            stop_node = A.BinOp(
                                op="-",
                                left=A.Call(
                                    func="len",
                                    args=[self._make_name_ref(subj_name, pos)],
                                    pos=pattern.pos,
                                ),
                                right=A.IntLit(value=n_after, pos=pattern.pos),
                                pos=pattern.pos,
                            )
                        slice_node = A.Slice(
                            start=A.IntLit(value=i, pos=pattern.pos),
                            stop=stop_node,
                            pos=pattern.pos,
                        )
                        sub_ref = A.Subscript(
                            obj=self._make_name_ref(subj_name, pos),
                            index=slice_node,
                            pos=pattern.pos,
                        )
                        seq_binds.append(A.Assign(target=sub.name, value=sub_ref, pos=sub.pos))
                    continue

                # Fixed-position element: front indices before star, back indices after.
                if star_index is None or i < star_index:
                    idx_expr: A.Expr = A.IntLit(value=i, pos=pattern.pos)
                else:
                    # How many fixed elements remain after position i (not counting i itself).
                    remaining = len(pattern.patterns) - i - 1
                    back_idx = -(remaining + 1)
                    idx_expr = A.IntLit(value=back_idx, pos=pattern.pos)

                elem_ref = A.Subscript(
                    obj=self._make_name_ref(subj_name, pos),
                    index=idx_expr,
                    pos=pattern.pos,
                )

                # Simple sub-patterns can reference elem_ref directly, no temp needed:
                # - MatchCapture: bind from elem_ref -> goes to binds (after test passes)
                # - MatchWildcard: nothing to do
                # - MatchValue: compare directly against elem_ref -> test only
                # Complex sub-patterns need a temp allocated BEFORE the test.
                if isinstance(sub, A.MatchCapture):
                    if sub.name != "_":
                        seq_binds.append(A.Assign(target=sub.name, value=elem_ref, pos=sub.pos))
                elif isinstance(sub, A.MatchValue):
                    elem_test = A.Compare(
                        ops=["=="],
                        operands=[elem_ref, sub.value],
                        pos=sub.pos,
                    )
                    seq_tests.append(elem_test)
                else:
                    # Complex pattern: create elem temp, hoist its assignment before
                    # the enclosing if's test so the sub-pattern can reference it.
                    elem_name = f"__match_elem_{id(pattern)}_{i}"
                    seq_pre.append(A.Assign(target=elem_name, value=elem_ref, pos=pattern.pos))
                    sub_pre, sub_test, sub_binds = self._lower_pattern(sub, elem_name, pattern.pos)
                    seq_pre.extend(sub_pre)
                    if not (isinstance(sub_test, A.IntLit) and sub_test.value == 1):
                        seq_tests.append(sub_test)
                    seq_binds.extend(sub_binds)

            return seq_pre, self._and_chain(seq_tests, pattern.pos), seq_binds

        if isinstance(pattern, A.MatchClass):
            cls_name = pattern.cls_name
            # isinstance(subject, ClassName) check.
            isinstance_call = A.Call(
                func="isinstance",
                args=[
                    self._make_name_ref(subj_name, pos),
                    A.Name(name=cls_name, pos=pattern.pos),
                ],
                pos=pattern.pos,
            )
            cls_tests: list = [isinstance_call]
            cls_pre: list = []
            cls_binds: list = []

            # Resolve positional patterns via __match_args__.
            if pattern.positional:
                match_args: list = []
                for c in self.mod.classes:
                    if c.name != cls_name:
                        continue
                    for cv_name, _annot, cv_val in getattr(c, "class_vars", []) or []:
                        if cv_name == "__match_args__" and cv_val is not None:
                            if isinstance(cv_val, A.TupleLit):
                                for elt in cv_val.elems:
                                    if isinstance(elt, A.StrLit):
                                        match_args.append(elt.value)
                            elif isinstance(cv_val, A.ListLit):
                                for elt in cv_val.elems:
                                    if isinstance(elt, A.StrLit):
                                        match_args.append(elt.value)
                if not match_args:
                    raise SemaError(
                        f"class '{cls_name}' does not define __match_args__ "
                        "for positional patterns",
                        pattern.pos,
                    )
                if len(pattern.positional) > len(match_args):
                    raise SemaError(
                        f"too many positional patterns for '{cls_name}' "
                        f"(__match_args__ has {len(match_args)} entries)",
                        pattern.pos,
                    )
                for i, sub in enumerate(pattern.positional):
                    attr_name = match_args[i]
                    attr_ref = A.Attr(
                        obj=self._make_name_ref(subj_name, pos),
                        name=attr_name,
                        pos=pattern.pos,
                    )
                    elem_name = f"__match_attr_{id(pattern)}_{attr_name}"
                    cls_pre.append(A.Assign(target=elem_name, value=attr_ref, pos=pattern.pos))
                    sub_pre, sub_test, sub_binds = self._lower_pattern(sub, elem_name, pattern.pos)
                    cls_pre.extend(sub_pre)
                    if not (isinstance(sub_test, A.IntLit) and sub_test.value == 1):
                        cls_tests.append(sub_test)
                    cls_binds.extend(sub_binds)

            # Keyword patterns.
            for attr_name, sub in pattern.kwargs:
                attr_ref = A.Attr(
                    obj=self._make_name_ref(subj_name, pos),
                    name=attr_name,
                    pos=pattern.pos,
                )
                elem_name = f"__match_kw_{id(pattern)}_{attr_name}"
                cls_pre.append(A.Assign(target=elem_name, value=attr_ref, pos=pattern.pos))
                sub_pre, sub_test, sub_binds = self._lower_pattern(sub, elem_name, pattern.pos)
                cls_pre.extend(sub_pre)
                if not (isinstance(sub_test, A.IntLit) and sub_test.value == 1):
                    cls_tests.append(sub_test)
                cls_binds.extend(sub_binds)

            return cls_pre, self._and_chain(cls_tests, pattern.pos), cls_binds

        if isinstance(pattern, A.MatchAs):
            if pattern.pattern is None:
                as_pre: list = []
                as_test = _TRUE
                as_binds: list = []
            else:
                as_pre, as_test, as_binds = self._lower_pattern(pattern.pattern, subj_name, pos)
            as_binds.append(
                A.Assign(
                    target=pattern.name,
                    value=self._make_name_ref(subj_name, pos),
                    pos=pattern.pos,
                )
            )
            return as_pre, as_test, as_binds

        raise SemaError(
            f"internal: unhandled pattern {type(pattern).__name__}", pos
        )

    def _check_tuple_assign_target(
        self, t: "A.Expr", value_t: str, scope: Scope, pos
    ) -> None:
        """Validate one target of a parallel-form TupleAssign against the
        already-checked type of its paired value, and bind `Name` targets
        into scope. Mirrors the equivalent checks in IndexAssign/AttrAssign
        (`xs[0], xs[1] = ...`, `self.x, self.y = ...`)."""
        if isinstance(t, A.Name):
            scope.add(t.name, value_t)
            return
        if isinstance(t, A.Subscript):
            self._check_expr(t.obj, scope)
            self._check_expr(t.index, scope)
            obj_t = A.expr_type(t.obj)
            if isinstance(t.index, A.Slice):
                return
            if obj_t == "list":
                el_t = self._list_el_type(t.obj, scope)
                if (
                    el_t not in ("?", "any", "int")
                    and value_t not in ("any", "int")
                    and value_t != el_t
                ):
                    raise SemaError(
                        f"list[i] = v: list element type is {el_t}, got {value_t}",
                        pos,
                    )
            elif obj_t == "dict":
                ikt = A.expr_type(t.index)
                if ikt not in ("str", "any", "int") and not ikt.startswith("instance:"):
                    raise SemaError("dict keys must be strings", pos)
                dvt = self._dict_value_type(t.obj, scope)
                if (
                    dvt not in ("any", "int")
                    and value_t not in ("any", "int")
                    and value_t != dvt
                    and not (dvt.startswith("instance:") and value_t.startswith("instance:"))
                ):
                    raise SemaError(
                        f"dict[k] = v: dict values are {dvt}, got {value_t}",
                        pos,
                    )
            elif obj_t == "any":
                pass  # opaque target: accept the index assignment leniently
            else:
                raise SemaError(f"cannot index a {obj_t}", pos)
            return
        # A.Attr
        if (
            isinstance(t.obj, A.Name)
            and t.obj.name in self.classes
            and self._class_var_type(t.obj.name, t.name) is not None
        ):
            return
        self._check_expr(t.obj, scope)
        obj_t = A.expr_type(t.obj)
        if not obj_t.startswith("instance:") and obj_t not in ("any", "module", "int"):
            raise SemaError(f"cannot assign attribute on {obj_t}", pos)
        if obj_t.startswith("instance:"):
            cls_name = obj_t.split(":", 1)[1]
            if cls_name in self.classes:
                resolved = self._resolve_method(cls_name, t.name)
                if resolved is not None and "property" in resolved[1].decorators:
                    raise SemaError(
                        f"property {t.name!r} of {cls_name!r} object has no setter",
                        pos,
                    )
                sig = self.classes[cls_name]
                if isinstance(t.obj, A.Name) and t.obj.name == "self":
                    if t.name not in sig.fields or (
                        sig.fields[t.name] == "int" and value_t not in ("int", "any")
                    ):
                        sig.fields[t.name] = value_t

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
            # A module-level function used as a value (passed, stored in a var).
            # Scope binding takes priority: if the user named a variable the same
            # as a merged stdlib function (e.g. `log = logging.getLogger(...)`
            # shadowing `logging.log`), the variable's type wins.
            if e.name in self.funcs and e.name not in scope:
                e.inferred_type = "any"
                return
            if e.name not in scope:
                if self.in_lifted:
                    e.inferred_type = "any"
                    return
                raise SemaError(f"undefined variable {e.name!r}", e.pos)
            e.inferred_type = scope.types[e.name]
            if e.inferred_type == "list":
                e.list_el_type = scope.list_el_types.get(e.name, "int")
                e.list_el_value_type = scope.list_el_value_types.get(e.name, "int")
            elif e.inferred_type == "dict":
                e.value_type = scope.dict_value_types.get(e.name, "int")
                e.inner_value_type = scope.dict_inner_value_types.get(e.name, "int")
            elif e.inferred_type == "tuple":
                e.tuple_elem_types = list(scope.tuple_elem_types.get(e.name, []))
            elif e.inferred_type == "int":
                e.is_bool = scope.bool_flags.get(e.name, False)
                e.is_none = scope.none_flags.get(e.name, False)
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
                    e.inferred_type = "str"  # type: ignore
                    return
                # `float + any` (e.g. an opaque list element added to a float
                # accumulator): numeric promotion still applies, so the result
                # is "float", not opaque -- otherwise codegen's int/float
                # dispatch for the enclosing assignment mistypes the result.
                if e.op not in ("&", "|", "^", "<<", ">>") and "float" in (lt, rt):
                    e.inferred_type = "float"  # type: ignore
                    return
                e.inferred_type = "any"  # type: ignore
                return
            # An operand that's an object instance may overload the operator via
            # a dunder (`Path / "sub"` -> `__truediv__`, `a + b` -> `__add__`).
            # Resolve it so the result is typed precisely (e.g. `Path / "x"` is
            # still `instance:Path`, letting `.exists()` etc. type-check on the
            # result); checked before the str/numeric branches so e.g.
            # `Path / "x"` doesn't read as a bad string operation.
            if lt.startswith("instance:") or rt.startswith("instance:"):
                fwd, rfl = DUNDER_BINOP.get(e.op, (None, None))
                resolved = None
                reflected = False
                if fwd is not None and lt.startswith("instance:"):
                    resolved = self._resolve_method(lt.split(":", 1)[1], fwd)
                if resolved is None and rfl is not None and rt.startswith("instance:"):
                    resolved = self._resolve_method(rt.split(":", 1)[1], rfl)
                    reflected = resolved is not None
                if resolved is not None:
                    owner, sig = resolved
                    if sig.arity != 2:
                        raise SemaError(
                            f"{owner}.{sig.name}() must take exactly (self, other)",
                            e.pos,
                        )
                    e.dunder_owner = owner  # type: ignore
                    e.dunder_method = sig.name  # type: ignore
                    e.dunder_reflected = reflected  # type: ignore
                    if sig.ret_type is not None:
                        ty, el, _val = sig.ret_type  # type: ignore
                        e.inferred_type = ty
                        if ty == "list" and el is not None:
                            e.list_el_type = el
                    else:
                        e.inferred_type = "any"  # type: ignore
                    return
                # No dunder found: opaque result rather than rejecting — the
                # receiver may be an external/unmodeled instance type.
                e.inferred_type = "any"  # type: ignore
                return
            # String operations: + concatenates two strings; * repeats a string
            # by an int count. Anything else involving strings is rejected.
            if "str" in (lt, rt):
                if e.op == "%" and lt == "str":
                    self._check_pct_format(e, scope)
                    return
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
                e.inferred_type = "type"  # type: ignore
                return
            # Set union/difference/intersection (|, -, &) returns a set.
            if e.op in ("|", "&", "-") and lt == "set" and rt == "set":
                e.inferred_type = "set"  # type: ignore
                return
            if e.op == "|=" and lt == "set":
                e.inferred_type = "set"  # type: ignore
                return
            # Dict union (PEP 584): `d1 | d2` builds a new dict containing
            # d1's entries with d2's entries merged in on top (d2 wins on
            # key conflicts).
            if e.op == "|" and lt == "dict" and rt == "dict":
                e.inferred_type = "dict"  # type: ignore
                return
            # List concatenation: list + list -> list.
            if e.op == "+" and lt in ("list", "any", "int") and rt in ("list", "any", "int") and "list" in (lt, rt):
                e.inferred_type = "list"  # type: ignore
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
                        # An "any" element kind (an opaque/unknown-element list)
                        # accepts any needle; likewise an "int" needle, which
                        # doubles as asmpython's unknown sentinel. Only flag a
                        # genuine concrete mismatch (e.g. `str in list[int]`).
                        if lt != el_t and el_t != "any" and lt != "int":
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
                        # those (asmpython's own membership tests are homogeneous).
                        ets = A.tuple_element_types(e.operands[i + 1])
                        # Distinct non-"any" element kinds, as a list (avoid a
                        # set + next(iter(...)) so this stays self-compilable).
                        kinds: list = []
                        for t in ets:
                            if t != "any" and t not in kinds:
                                kinds.append(t)
                        if len(kinds) > 1:
                            raise SemaError(
                                "'in' on a heterogeneous tuple is unsupported",
                                e.pos,
                            )
                        # "int" doubles as the unknown sentinel, so it's a lenient
                        # needle (asmpython's shallow inference types many strings
                        # as int).
                        if kinds and lt not in ("any", "int") and lt not in kinds:
                            only = kinds[0]
                            raise SemaError(
                                f"'{op}': needle is {lt} but tuple elements are {only}",
                                e.pos,
                            )
                        continue
                    if rt == "set":
                        # `x in {…}`: sets only model membership; the element
                        # kind isn't tracked, so accept any needle.
                        continue
                    if rt in ("any", "int") or rt.startswith("instance:"):
                        # Membership against an opaque value (`any`), the unknown-
                        # `int` sentinel, or a user instance — e.g. a property/
                        # field asmpython can't model (`x in scope.names`) or an
                        # instance with `__contains__` (`x in scope`). All are
                        # dict-backed at runtime, so codegen lowers this via dict
                        # membership; stay lenient rather than erroring on a
                        # container we couldn't type precisely.
                        continue
                    raise SemaError(
                        f"'{op}' not supported between {lt} and {rt}",
                        e.pos,
                    )
                if op in ("is", "is not"):
                    # asmpython has no `None`-as-distinct-value yet. `x is None`
                    # therefore lowers to `x == 0`. Accept any operand types
                    # — the comparison happens at the raw 8-byte level.
                    continue
                if op in ("==", "!=") and (
                    lt.startswith("instance:") or rt.startswith("instance:")
                ):
                    # `a == b` / `a != b` where either side is a user
                    # instance: dispatch to a user-defined `__eq__` if the
                    # class has one, mirroring DUNDER_BINOP's resolution for
                    # arithmetic operators. CPython's default `__ne__` is
                    # `not __eq__`, so `!=` reuses `__eq__` and negates the
                    # result. Only handled for a single (non-chained)
                    # comparison, matching codegen's scratch-slot allocation.
                    cls = (
                        lt.split(":", 1)[1]
                        if lt.startswith("instance:")
                        else rt.split(":", 1)[1]
                    )
                    resolved = self._resolve_method(cls, "__eq__")
                    if resolved is not None and len(e.ops) == 1:
                        owner, sig = resolved
                        if sig.arity != 2:
                            raise SemaError(
                                f"{owner}.__eq__() must take exactly (self, other)",
                                e.pos,
                            )
                        e.dunder_owner = owner  # type: ignore
                        e.dunder_method = "__eq__"  # type: ignore
                        e.dunder_negate = (op == "!=")  # type: ignore
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
                        # literal, common in asmpython's own source). Ordering and
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
                # sentinel, which doubles as asmpython's unknown type. Let the
                # concrete arm win so the result keeps a useful type.
                e.inferred_type = ot if bt == "int" else bt
            else:
                raise SemaError(
                    f"conditional expression arms have mismatched types ({bt} vs {ot})",
                    e.pos,
                )
            return
        if isinstance(e, A.NamedExpr):
            # `target := value` (the walrus operator): check + type the value,
            # bind `target` exactly as `target = value` would, and the whole
            # expression takes on `value`'s type.
            self._check_expr(e.value, scope)
            self._bind_name_from_value(e.target, e.value, scope)
            e.inferred_type = A.expr_type(e.value)
            if e.inferred_type == "list":
                e.list_el_type = self._list_el_type(e.value, scope)
            return
        if isinstance(e, A.Call):
            self._check_call(e, scope)
            return
        if isinstance(e, A.ListLit):
            seen: str | None = None
            for el in e.elems:
                self._check_expr(el, scope)
                et = A.expr_type(el)
                # Every asmpython value is a uniform 8-byte slot, so a list may
                # hold nested collections (list/dict/tuple/set) and instances as
                # well as scalars — they're stored as pointers (mirrors what
                # TupleLit and list.append already accept).
                if et not in (
                    "int",
                    "str",
                    "float",
                    "any",
                    "tuple",
                    "list",
                    "dict",
                    "set",
                ) and not et.startswith("instance:"):
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
            # When elements are nested containers (list[dict] / list[list]),
            # remember the common leaf kind so `xs[i][k]` / `for x in xs: x[k]`
            # recover the value type one level down.
            if seen in ("dict", "list"):
                e.el_value_type = self._common_container_inner(e.elems, scope)
            # When elements are tuples, remember the common per-slot kinds so
            # `xs[i][0]` and `for a, b in xs` resolve the slot types.
            elif seen == "tuple":
                e.el_tuple_types = self._common_tuple_slots(e.elems, scope)
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
                raise SemaError(f"cannot iterate a {it_t} in a comprehension", e.pos)
            # A child scope so the loop variable doesn't leak.
            child = Scope()
            child.types.update(scope.types)
            child.list_el_types.update(scope.list_el_types)
            child.dict_value_types.update(scope.dict_value_types)
            child.dict_inner_value_types.update(scope.dict_inner_value_types)
            child.tuple_elem_types.update(scope.tuple_elem_types)
            self._bind_comprehension_targets(e, el, child)
            loop_vars = set(self._flat_target_names(e.targets)) if e.targets else {e.var}
            if e.cond is not None:
                self._check_expr(e.cond, child)
            self._check_expr(e.elt, child)
            e.inferred_type = "list"
            e.list_el_type = A.expr_type(e.elt)
            self._merge_walrus_bindings(scope, child, loop_vars)
            return
        if isinstance(e, A.DictComprehension):
            self._check_expr(e.iter, scope)
            it_t = A.expr_type(e.iter)
            if it_t == "list":
                el = self._list_el_type(e.iter, scope)
            elif it_t in ("str", "dict"):
                el = "str"
            elif it_t == "tuple":
                ets = A.tuple_element_types(e.iter)
                el = ets[0] if ets else "int"
            elif it_t == "any":
                el = "any"
            else:
                raise SemaError(
                    f"cannot iterate a {it_t} in a dict comprehension", e.pos
                )
            child = Scope()
            child.types.update(scope.types)
            child.list_el_types.update(scope.list_el_types)
            child.dict_value_types.update(scope.dict_value_types)
            child.dict_inner_value_types.update(scope.dict_inner_value_types)
            child.tuple_elem_types.update(scope.tuple_elem_types)
            self._bind_comprehension_targets(e, el, child)
            loop_vars = set(self._flat_target_names(e.targets)) if e.targets else {e.var}
            if e.cond is not None:
                self._check_expr(e.cond, child)
            self._check_expr(e.key, child)
            if A.expr_type(e.key) not in ("str", "any"):
                raise SemaError(
                    "dict comprehension keys must be strings "
                    "(other types not supported yet)",
                    getattr(e.key, "pos", e.pos),
                )
            self._check_expr(e.value, child)
            vt = A.expr_type(e.value)
            if vt not in (
                "int",
                "str",
                "float",
                "any",
                "tuple",
                "dict",
                "list",
                "set",
            ) and not vt.startswith("instance:"):
                raise SemaError(
                    f"dict comprehension value of type {vt} is not supported yet",
                    getattr(e.value, "pos", e.pos),
                )
            e.inferred_type = "dict"
            e.value_type = vt if vt != "any" else "int"
            self._merge_walrus_bindings(scope, child, loop_vars)
            return
        if isinstance(e, A.DictLit):
            for k, v in zip(e.keys, e.values):
                if k is None:
                    # `**other` (PEP 448 dict unpacking): `other` must itself
                    # be dict-typed (or opaque).
                    self._check_expr(v, scope)
                    vt = A.expr_type(v)
                    if vt not in ("dict", "any"):
                        raise SemaError(
                            f"dict unpacking requires a dict (got {vt})",
                            getattr(v, "pos", e.pos),
                        )
                    continue
                self._check_expr(k, scope)
                if A.expr_type(k) not in ("str", "any", "int") and not A.expr_type(k).startswith("instance:"):
                    raise SemaError(
                        "dict keys must be strings (other types not supported yet)",
                        getattr(k, "pos", e.pos),
                    )
            # Dict values must be homogeneous: all int, all str, all float, all
            # instances of one class, or all of one pointer-sized collection
            # kind (dict / list / set / tuple). Nested collections are stored
            # as heap pointers, which fit the same uniform 8-byte slot. The
            # value kind is tracked on the DictLit so codegen / iteration / a
            # chained read (`d[k][k2]`) can recover it.
            seen_v: str | None = None
            for k, v in zip(e.keys, e.values):
                if k is None:
                    # A `**other` spread contributes `other`'s value kind too,
                    # so e.g. `{**d1, "x": 1}` where `d1: dict[str, str]` and
                    # the literal key is `int` collapses to "any" below, same
                    # as any other value-kind mismatch. An opaque `other`
                    # ("any"-typed dict) is compatible with any value kind.
                    vt = "any" if A.expr_type(v) == "any" else self._dict_value_type(v, scope)
                else:
                    self._check_expr(v, scope)
                    vt = A.expr_type(v)
                    if vt not in (
                        "int",
                        "str",
                        "float",
                        "any",
                        "tuple",
                        "dict",
                        "list",
                        "set",
                    ) and not vt.startswith("instance:"):
                        raise SemaError(
                            f"dict value of type {vt} is not supported yet",
                            getattr(v, "pos", e.pos),
                        )
                if vt == "any":
                    continue  # opaque value: compatible with any value kind
                if seen_v is None or seen_v == "any":
                    seen_v = vt
                elif seen_v != vt:
                    # Two different value kinds. If both are pointer-sized
                    # (instances / nested collections / the int-unknown
                    # sentinel — anything but a float, which lives in xmm), the
                    # dict still has a uniform 8-byte slot layout: collapse the
                    # value kind to opaque ("any") rather than rejecting. A real
                    # float-vs-pointer mix stays an error (register-class clash).
                    if "float" in (seen_v, vt):
                        raise SemaError(
                            f"mixed dict value types ({seen_v} and {vt}); "
                            "a float value can't share a dict with non-floats",
                            getattr(v, "pos", e.pos),
                        )
                    seen_v = "any"
            e.value_type = seen_v if seen_v is not None else "int"
            # When the values are themselves dicts/lists, record their common
            # inner value/element kind, so a chained `outer[k][k2]` read can
            # recover the leaf type (one nesting level deep).
            if e.value_type in ("dict", "list"):
                e.inner_value_type = self._common_container_inner(e.values, scope)
            # When the values are themselves tuples, record their common
            # per-slot shape (if every value agrees), so `d.values()` /
            # `d.items()` can type unpacked targets (`for k, v in d.items()`).
            if e.value_type == "tuple":
                shapes = [self._tuple_elem_types(v, scope) for v in e.values]
                if shapes and all(s == shapes[0] and s for s in shapes):
                    e.value_tuple_elem_types = shapes[0]
            return
        if isinstance(e, A.TupleLit):
            ets: list[str] = []
            for el in e.elems:
                self._check_expr(el, scope)
                et = A.expr_type(el)
                # Every asmpython value is a uniform 8-byte slot, so a tuple may
                # hold any of them — including nested collections (which are
                # pointers). The per-slot kind is tracked for later indexing.
                if et not in (
                    "int",
                    "str",
                    "float",
                    "any",
                    "tuple",
                    "list",
                    "dict",
                    "set",
                    # A class used as a value (`(LinuxCodegen, "elf64")`): it
                    # lowers to the class's RTTI id, an ordinary 8-byte slot.
                    "type",
                ) and not et.startswith("instance:"):
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
            # `expr_type` already reports a SetLit as "set". Sets are
            # str-keyed in v1 (the backing dict's hash/equality assume a
            # string pointer); a non-str element would hash/compare a raw
            # int as if it were a pointer and crash at runtime.
            for el in e.elems:
                self._check_expr(el, scope)
                et = A.expr_type(el)
                if et not in ("str", "any"):
                    raise SemaError(
                        f"set elements of type {et} are not supported yet "
                        "(sets are str-keyed in v1)",
                        getattr(el, "pos", e.pos),
                    )
            return
        if isinstance(e, A.Subscript):
            self._check_expr(e.obj, scope)
            obj_t = A.expr_type(e.obj)
            if isinstance(e.index, A.Slice):
                if obj_t not in ("str", "list", "any", "int"):
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
                # A nested container element (list[dict] / list[list]): carry the
                # tracked leaf kind onto the read-out container so `xs[i][k]`
                # recovers the value type. Falls back to "any" when untracked.
                if e.inferred_type in ("dict", "list"):
                    inner = self._list_el_value_type(e.obj, scope)
                    inner = inner if inner != "int" else "any"
                    e.value_type = inner
                    e.list_el_type = inner
                elif e.inferred_type == "tuple":
                    # list[tuple]: carry the per-slot kinds so `xs[i][0]` types.
                    e.tuple_elem_types = self._list_el_tuple_types(e.obj, scope)
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
                    # An "int" slot is asmpython's unknown sentinel — a slot
                    # holding an inferred-but-untracked object (e.g. a FuncSig
                    # in a `(name, sig)` return). Index it as opaque so
                    # `t[i].attr` stays lenient (mirrors the tuple-unpack path).
                    slot = ets[idx]
                    e.inferred_type = "any" if slot == "int" else slot
                elif _all_same(ets):
                    # Dynamic index is only well-typed on a homogeneous tuple.
                    e.inferred_type = ets[0]
                else:
                    raise SemaError(
                        "tuple index must be a constant for a heterogeneous tuple",
                        e.pos,
                    )
            elif obj_t == "dict":
                # "int" doubles as the unknown sentinel; lenient (see above).
                if A.expr_type(e.index) not in ("str", "any", "int"):
                    raise SemaError("dict keys must be strings", e.pos)
                e.inferred_type = self._dict_value_type(e.obj, scope)
                # A nested container value (dict[str, dict] / dict[str, list]):
                # carry the outer dict's tracked inner kind onto the read-out
                # container, so `outer[k][k2]` recovers the leaf type. Falls
                # back to "any" (lenient) when the inner kind wasn't tracked.
                if e.inferred_type in ("dict", "list"):
                    inner = self._dict_inner_value_type(e.obj, scope)
                    inner = inner if inner != "int" else "any"
                    e.value_type = inner
                    e.list_el_type = inner
            elif obj_t == "str":
                if A.expr_type(e.index) != "int":
                    raise SemaError("string index must be an int", e.pos)
                e.inferred_type = "str"
            elif obj_t == "any":
                # Indexing an opaque value stays opaque.
                e.inferred_type = "any"
            elif obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                cls_sig = self.classes.get(cls_name)
                msig = None
                if cls_sig is not None:
                    msig = cls_sig.methods.get("__getitem__")
                if msig is None:
                    raise SemaError(
                        f"'{cls_name}' object does not support indexing", e.pos
                    )
                # Mark so codegen translates this subscript into a __getitem__ call.
                e._getitem_class = cls_name  # type: ignore[attr-defined]
                if msig.ret_type is not None:
                    ty, el, _val = msig.ret_type  # type: ignore[misc]
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el  # type: ignore[attr-defined]
                else:
                    e.inferred_type = "int"
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
            # Class-level variable read: `ClassName.x` (static constant). Type it
            # from the class var's default expression.
            if isinstance(e.obj, A.Name) and e.obj.name in self.classes:
                cvt = self._class_var_type(e.obj.name, e.name)
                if cvt is not None:
                    e.inferred_type = cvt
                    return
            # Special-case module attribute: math.pi, math.sqrt(...).
            if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
                bindings = self.imported_modules[e.obj.name]
                if e.name not in bindings:
                    # An attribute the curated registry doesn't model (e.g.
                    # `os.environ`, `os.sep`). The real CPython module has it;
                    # stay lenient (opaque) rather than erroring, so source that
                    # uses unmodeled module attributes still type-checks.
                    e.inferred_type = "any"
                    return
                b = bindings[e.name]
                if isinstance(b, stdlib.Func):
                    e.inferred_type = b.ret_type
                else:
                    e.inferred_type = b.ty
                    if b.ty == "list":
                        e.list_el_type = b.el_type or "any"
                return
            # Instance field access: self.x, point.x — typed as int for v1
            # (all attribute values are int, since instances use a str->int dict).
            self._check_expr(e.obj, scope)
            obj_t = A.expr_type(e.obj)
            if obj_t.startswith("instance:"):
                # A field of a user instance carries the type sema inferred for
                # it (str / instance / list / ... ). An undeclared field (one
                # never assigned in __init__) defaults to "any": codegen reads
                # it via the same dict_get_default regardless, and "any" keeps
                # later `.method()`/binop use lenient instead of rejecting it
                # as "int has no method ..." — needed for dynamically-populated
                # instances like argparse's Namespace (`args.source.exists()`).
                # A field of an external/imported instance is also "any".
                cls = obj_t.split(":", 1)[1]
                if cls in self.classes:
                    # `@property`: `obj.x` (no call parens) invokes the
                    # zero-arg getter method `x`, not a field read. Rewrite
                    # this Attr node into an equivalent no-arg MethodCall in
                    # place, so codegen's existing method-dispatch (including
                    # virtual dispatch for overridden properties) handles it.
                    resolved = self._resolve_method(cls, e.name)
                    if resolved is not None and "property" in resolved[1].decorators:
                        _owner, sig = resolved
                        obj_expr = e.obj
                        e.__class__ = A.MethodCall  # type: ignore[assignment]
                        e.obj = obj_expr  # type: ignore[attr-defined]
                        e.method = e.name  # type: ignore[attr-defined]
                        e.args = []  # type: ignore[attr-defined]
                        e.kwargs = []  # type: ignore[attr-defined]
                        e.list_el_type = "int"  # type: ignore[attr-defined]
                        e.value_type = "int"  # type: ignore[attr-defined]
                        e.tuple_elem_types = []  # type: ignore[attr-defined]
                        if sig.ret_tuple is not None:
                            e.inferred_type = "tuple"
                            e.tuple_elem_types = list(sig.ret_tuple)  # type: ignore[attr-defined]
                        elif sig.ret_type is not None:
                            ty, el, _val = sig.ret_type  # type: ignore[misc]
                            e.inferred_type = ty
                            if ty == "list" and el is not None:
                                e.list_el_type = el  # type: ignore[attr-defined]
                        else:
                            e.inferred_type = "int"
                        return
                    ft = self._resolve_field_type(cls, e.name)
                    e.inferred_type = ft if ft is not None else "any"
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
                # Attribute of a module asmpython doesn't model (e.g.
                # `sys.stderr`), or of an already-opaque value. Stay lenient.
                e.inferred_type = "any"
                return
            if obj_t == "str":
                # Attribute (not method) access on a str. asmpython's
                # `except ... as e` binds the message as a str, but source that
                # treats the caught value as an exception *object* reads fields
                # off it (`e.args`, `e.phase`). Method calls go through the
                # MethodCall path; this is bare attribute access, so model the
                # result opaque rather than erroring.
                e.inferred_type = "any"
                return
            # Unknown type (e.g. an unannotated parameter holding a complex object):
            # be lenient rather than rejecting valid self-hosting code.
            e.inferred_type = "any"
            return
        if isinstance(e, A.MethodCall):
            e.args = self._expand_starred_args(e.args, scope)
            # Interpreter-only calls (dynamic import, code-exec by string) have
            # no native lowering — reject early with a located message instead
            # of letting them reach codegen as a raw NotImplementedError.
            if (
                isinstance(e.obj, A.Name)
                and (e.obj.name, e.method) in INTERPRETER_ONLY_METHODS
            ):
                raise SemaError(
                    f"{e.obj.name}.{e.method}() is not supported: dynamic import "
                    "requires a Python interpreter and cannot be compiled to "
                    "native code",
                    e.pos,
                )
            # Module function call: math.sqrt(x), math.pow(a, b).
            if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
                # os.getcwd() / os.listdir(path): inline codegen helpers not in
                # BINDINGS (no C symbol). Give them proper types and accept args.
                if e.obj.name == "os" and e.method == "getcwd":
                    e.inferred_type = "str"
                    return
                if e.obj.name == "os" and e.method == "listdir":
                    if e.args:
                        self._check_expr(e.args[0], scope)
                    e.inferred_type = "list"
                    e.list_el_type = "str"
                    return
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
            # `module.Thing(args)` where `module` is a merged *project* module
            # (not an FFI registry module) and `Thing` is a merged class or
            # top-level function (`stdlib.Func(...)`, `pkgformat.load_package(p)`):
            # whole-program compilation flattens the namespace, so this IS a
            # plain call to the merged symbol. Rewrite the node into an A.Call
            # in place and run the ordinary call checker — constructors get the
            # full kwarg/dataclass handling, functions their signature typing,
            # and codegen sees a plain Call with its usual slot reservations.
            if (
                isinstance(e.obj, A.Name)
                and e.obj.name not in self.imported_modules
                and scope.types.get(e.obj.name) == "module"
                and (e.method in self.classes or e.method in self.funcs)
            ):
                e.__class__ = A.Call  # type: ignore[assignment]
                e.func = e.method  # type: ignore[attr-defined]
                self._check_call(e, scope)  # type: ignore[arg-type]
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
                    # Every asmpython value is a uniform 8-byte slot, so a list
                    # may hold nested collections (list/dict/tuple/set) and
                    # instances as well as scalars — they're stored as pointers.
                    # (Mirrors what TupleLit already accepts for its elements.)
                    if arg_t not in (
                        "int",
                        "str",
                        "float",
                        "any",
                        "tuple",
                        "list",
                        "dict",
                        "set",
                    ) and not arg_t.startswith("instance:"):
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
                    elif el_t not in ("any", "int") and arg_t != el_t:
                        # `int` doubles as the unknown element sentinel (e.g. a
                        # list produced by `list(<opaque>)` whose real element
                        # kind we never tracked), so don't reject a mismatch
                        # against it — only flag a genuine concrete clash.
                        raise SemaError(
                            f"list.append() expected {el_t}, got {arg_t}",
                            e.pos,
                        )
                    e.inferred_type = "int"  # returns None ~ 0
                elif e.method == "pop":
                    if len(e.args) > 1:
                        raise SemaError("list.pop() takes at most 1 argument", e.pos)
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
                elif e.method == "index":
                    # xs.index(v) -> position of the first matching element.
                    if len(e.args) != 1:
                        raise SemaError("list.index() takes 1 argument", e.pos)
                    e.inferred_type = "int"
                elif e.method == "sort":
                    if e.args:
                        raise SemaError("list.sort() takes no arguments", e.pos)
                    self._check_sort_kwargs(e, scope)
                    e.inferred_type = "int"  # in-place, returns None ~ 0
                elif e.method == "reverse":
                    if e.args:
                        raise SemaError("list.reverse() takes no arguments", e.pos)
                    e.inferred_type = "int"
                elif e.method == "count":
                    if len(e.args) != 1:
                        raise SemaError("list.count() takes 1 argument", e.pos)
                    e.inferred_type = "int"
                elif e.method == "clear":
                    if e.args:
                        raise SemaError("list.clear() takes no arguments", e.pos)
                    e.inferred_type = "int"
                elif e.method == "copy":
                    if e.args:
                        raise SemaError("list.copy() takes no arguments", e.pos)
                    e.inferred_type = "list"
                    e.list_el_type = el_t if el_t not in ("?", "") else "int"
                elif e.method == "insert":
                    if len(e.args) != 2:
                        raise SemaError("list.insert() takes (index, value)", e.pos)
                    e.inferred_type = "int"
                elif e.method == "remove":
                    if len(e.args) != 1:
                        raise SemaError("list.remove() takes 1 argument", e.pos)
                    e.inferred_type = "int"
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
                    kt = A.expr_type(e.args[0])
                    if kt not in ("str", "any", "int") and not kt.startswith("instance:"):
                        raise SemaError("dict.get() key must be a str", e.pos)
                    e.inferred_type = self._dict_value_type(e.obj, scope)
                elif e.method == "contains":
                    if len(e.args) != 1:
                        raise SemaError("dict.contains() takes 1 argument", e.pos)
                    kt = A.expr_type(e.args[0])
                    if kt not in ("str", "any", "int") and not kt.startswith("instance:"):
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
                    # Element kind = the dict's value kind (so `for v in d.values()`
                    # and `d.values()[i].attr` recover it; opaque dicts -> any).
                    e.list_el_type = self._dict_value_type(e.obj, scope)
                    if e.list_el_type == "tuple":
                        # Per-slot shape for `for a, b in d.values()` target
                        # typing, when the dict's value tuples share a shape.
                        e.tuple_elem_types = self._dict_value_tuple_types(
                            e.obj, scope
                        )
                elif e.method == "items":
                    # d.items() -> a list of (key, value) pair tuples. The pair
                    # slots aren't tracked per-entry; `for k, v in d.items()`
                    # binds the targets leniently via the multi-target unpack.
                    if e.args:
                        raise SemaError("dict.items() takes no arguments", e.pos)
                    e.inferred_type = "list"
                    e.list_el_type = "tuple"
                    # Pair shape for `for k, v in d.items()` target typing.
                    e.tuple_elem_types = ["str", self._dict_value_type(e.obj, scope)]
                elif e.method == "update":
                    # d.update(other): merge another dict in. Lenient on the
                    # argument kind; returns None (~0).
                    if len(e.args) != 1:
                        raise SemaError("dict.update() takes 1 argument", e.pos)
                    e.inferred_type = "int"
                elif e.method == "pop":
                    # d.pop(key[, default]) -> removes key, returns its value
                    # (or the default if absent). Returns the dict's value kind.
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError(
                            "dict.pop() takes (key) or (key, default)", e.pos
                        )
                    if A.expr_type(e.args[0]) not in ("str", "any"):
                        raise SemaError("dict.pop() key must be a str", e.pos)
                    e.inferred_type = self._dict_value_type(e.obj, scope)
                elif e.method == "clear":
                    if e.args:
                        raise SemaError("dict.clear() takes no arguments", e.pos)
                    e.inferred_type = "int"
                elif e.method == "copy":
                    if e.args:
                        raise SemaError("dict.copy() takes no arguments", e.pos)
                    e.inferred_type = "dict"
                    e.value_type = self._dict_value_type(e.obj, scope)
                elif e.method == "setdefault":
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError(
                            "dict.setdefault() takes (key[, default])", e.pos
                        )
                    if A.expr_type(e.args[0]) not in ("str", "any"):
                        raise SemaError("dict.setdefault() key must be a str", e.pos)
                    e.inferred_type = self._dict_value_type(e.obj, scope)
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
                    raise SemaError(f"{parent} has no method {e.method!r}", e.pos)
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
                    ty, el, _val = sig.ret_type  # type: ignore
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
                # Assembly operand validation: when string literals are passed
                # to any Assembly method, validate them at compile time.
                if class_name == "Assembly":
                    for _a in e.args:
                        if isinstance(_a, A.StrLit):
                            _check_asm_operand_lit(_a.value, _a.pos)
                # Return type priority: an inferred `return a, b` tuple shape
                # (so `x, y = obj.m()` unpacks), then an explicit annotation,
                # else int.
                if sig.ret_tuple is not None:
                    e.inferred_type = "tuple"
                    e.tuple_elem_types = list(sig.ret_tuple)  # type: ignore
                elif sig.ret_type is not None:
                    ty, el, _val = sig.ret_type  # type: ignore
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el
                        if el == "tuple" and sig.ret_list_tuple_types:
                            e.tuple_elem_types = list(sig.ret_list_tuple_types)  # type: ignore
                elif sig.returns_self:
                    # `def m(self): ... return self` with no annotation: the
                    # call's result is another reference to the receiver's
                    # type (e.g. `__enter__` returning `self`).
                    e.inferred_type = obj_t
                else:
                    e.inferred_type = "int"
            elif obj_t == "module" and e.method in self.funcs:
                # A module-qualified call to a merged project function
                # (`pkgformat.load_package(p)`, `ospath.join(a, b)`): adopt the
                # function's signature so the result is typed like a plain call
                # (codegen dispatches it to the merged symbol).
                msig = self.funcs[e.method]
                if msig.ret_tuple is not None:
                    e.inferred_type = "tuple"
                    e.tuple_elem_types = list(msig.ret_tuple)  # type: ignore
                elif msig.ret_type is not None:
                    mty, mel, mval = msig.ret_type  # type: ignore
                    e.inferred_type = mty
                    if mty == "list" and mel is not None:
                        e.list_el_type = mel
                    elif mty == "dict" and mval is not None:
                        e.value_type = mval
                else:
                    e.inferred_type = "int"
            elif obj_t in ("module", "any"):
                # A method on a module asmpython doesn't model (e.g.
                # `argparse.ArgumentParser(...)` — imported but outside the
                # stdlib registry), or on an already-opaque value. Stay lenient;
                # the result is opaque so chains keep type-checking.
                e.inferred_type = "any"
            elif obj_t == "set":
                if e.method in ("add", "discard", "remove"):
                    arg_t = A.expr_type(e.args[0])
                    if arg_t not in ("str", "any"):
                        raise SemaError(
                            f"set.{e.method}({arg_t}) is not supported yet "
                            "(sets are str-keyed in v1)",
                            e.args[0].pos,
                        )
                    e.inferred_type = "int"
                elif e.method in ("update", "clear"):
                    e.inferred_type = "int"
                elif e.method in ("union", "intersection", "difference"):
                    if len(e.args) != 1:
                        raise SemaError(
                            f"set.{e.method}() takes 1 argument", e.pos
                        )
                    e.inferred_type = "set"
                elif e.method == "copy":
                    if e.args:
                        raise SemaError("set.copy() takes no arguments", e.pos)
                    e.inferred_type = "set"
                elif e.method == "pop":
                    if e.args:
                        raise SemaError("set.pop() takes no arguments", e.pos)
                    e.inferred_type = "str"
                else:
                    e.inferred_type = "any"
            elif isinstance(e.obj, A.Name) and e.obj.name in self.classes:
                # `ClassName.method(args)`: a @staticmethod / @classmethod called
                # on the class itself (no instance). Validate against the method
                # signature; static methods take their args verbatim, class
                # methods take an implicit leading `cls`.
                cls_name = e.obj.name
                resolved = self._resolve_method(cls_name, e.method)
                if resolved is None:
                    raise SemaError(
                        f"{cls_name} has no method {e.method!r}", e.pos
                    )
                _owner, sig = resolved
                deco = getattr(sig, "decorators", [])
                if "classmethod" in deco:
                    expected = sig.arity - 1  # drop implicit cls
                elif "staticmethod" in deco:
                    expected = sig.arity
                else:
                    raise SemaError(
                        f"{cls_name}.{e.method}() needs an instance "
                        "(not a @staticmethod or @classmethod)",
                        e.pos,
                    )
                required = expected - sig.n_defaults
                if not (required <= len(e.args) <= expected):
                    raise SemaError(
                        f"{cls_name}.{e.method}() takes {required}..{expected} "
                        f"argument(s), got {len(e.args)}",
                        e.pos,
                    )
                for a in e.args:
                    self._check_expr(a, scope)
                if sig.ret_type is not None:
                    ty, el, _val = sig.ret_type  # type: ignore
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el
                else:
                    e.inferred_type = "int"
            else:
                raise SemaError(f"{obj_t} has no method {e.method!r}", e.pos)
            return
        if isinstance(e, A.Lambda):
            # Lambda: a small anonymous function whose body is a single expr.
            # We synthesise a hidden module-level function so codegen can emit
            # it, then return a function-pointer ("any") at the call site.
            import uuid as _uuid
            lname = f"_lambda_{_uuid.uuid4().hex[:8]}"
            e.func_name = lname  # type: ignore[attr-defined]
            inner_scope = Scope()
            # Seed with outer scope's names so captured variables resolve.
            for nm, ty in scope.types.items():
                inner_scope.add(nm, ty)
            for p in e.params:
                inner_scope.add(p, "any")
            ret_t = "int"
            if e.body is not None:
                try:
                    self._check_expr(e.body, inner_scope)
                    ret_t = A.expr_type(e.body)
                except Exception:
                    pass
            e.lambda_ret = ret_t  # type: ignore[attr-defined]
            e.inferred_type = "any"
            return
        if isinstance(e, A.Slice):
            if e.start is not None:
                self._check_expr(e.start, scope)
            if e.stop is not None:
                self._check_expr(e.stop, scope)
            if e.step is not None:
                self._check_expr(e.step, scope)
            e.inferred_type = "any"
            return
        raise SemaError(
            f"internal: unhandled expr {type(e).__name__}", getattr(e, "pos", None)
        )

    # Signature: (arg-types, return-type). The arg-types tuple may be empty.
    STR_METHODS = {
        "upper": ((), "str"),
        "lower": ((), "str"),
        "casefold": ((), "str"),
        "capitalize": ((), "str"),
        "swapcase": ((), "str"),
        "title": ((), "str"),
        "strip": ((), "str"),
        "lstrip": ((), "str"),
        "rstrip": ((), "str"),
        "startswith": (("str",), "int"),
        "endswith": (("str",), "int"),
        "removeprefix": (("str",), "str"),
        "removesuffix": (("str",), "str"),
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

    def _check_pct_format(self, e: A.BinOp, scope: Scope) -> None:
        """`"...%s..." % (args)` (or `% single_arg`) — printf-style formatting.

        The format string must be a literal (codegen lowers it to a concat
        chain, like .format()). Validates the argument count against the
        number of conversions and that each conversion's argument type makes
        sense (`%d`/`%x`/etc. need int, `%f`/etc. need a number; `%s`/`%r`
        accept anything).
        """
        if not isinstance(e.left, A.StrLit):
            raise SemaError(
                "'%' string formatting requires a literal format string", e.pos
            )
        try:
            pieces, nconv = A.parse_pct_format(e.left.value)
        except ValueError as exc:
            raise SemaError(f"bad format string: {exc}", e.pos)
        args = e.right.elems if isinstance(e.right, A.TupleLit) else [e.right]
        if len(args) != nconv:
            raise SemaError(
                f"'%' format string expects {nconv} argument(s), got {len(args)}",
                e.pos,
            )
        ai = 0
        for piece in pieces:
            if piece[0] != "arg":
                continue
            conv = piece[4]
            t = A.expr_type(args[ai])
            if conv in "dioxX" and t not in ("int", "any"):
                raise SemaError(f"'%{conv}' format requires an int argument", e.pos)
            if conv in "eEfFgG" and t not in ("int", "float", "any"):
                raise SemaError(f"'%{conv}' format requires a numeric argument", e.pos)
            ai += 1
        e.inferred_type = "str"  # type: ignore

    def _check_sort_kwargs(self, e, scope: Scope) -> None:
        """Validate and resolve the `key=`/`reverse=` kwargs shared by
        `sorted()`, `min()`/`max()`, and `list.sort()`.

        Only `key=<lambda literal>` and `key=<name bound to a lambda>` are
        supported (a bare named-function reference currently segfaults via
        the same indirect-call path used elsewhere, so it's rejected here
        too). Stamps `e.sort_key` (Optional[expr]), `e.sort_key_ret`
        ("str"/"int"), and `e.sort_reverse` (Optional[expr]), then clears
        `e.kwargs` so normal call-arg checks don't see them.
        """
        key_expr = None
        reverse_expr = None
        for kname, kexpr in e.kwargs:
            if kname == "key":
                key_expr = kexpr
            elif kname == "reverse":
                reverse_expr = kexpr
            else:
                raise SemaError(f"unexpected keyword argument {kname!r}", e.pos)
        if key_expr is not None:
            self._check_expr(key_expr, scope)
            if isinstance(key_expr, A.Lambda):
                ret_t = getattr(key_expr, "lambda_ret", "int")
            elif isinstance(key_expr, A.Name):
                if key_expr.name not in self.lambda_rets:
                    raise SemaError(
                        "key= must be a lambda literal or a name bound to a "
                        f"lambda (a bare function reference like {key_expr.name!r} "
                        "isn't supported)",
                        e.pos,
                    )
                ret_t = self.lambda_rets[key_expr.name]
            else:
                raise SemaError(
                    "key= must be a lambda literal or a name bound to a lambda",
                    e.pos,
                )
            # Lambda params are typed "any", so most non-str results (int,
            # float-as-bits, "any") compare correctly as ints; only an
            # explicit "str" result needs the string comparator.
            key_ret = "str" if ret_t == "str" else "int"
            e.sort_key = key_expr  # type: ignore[attr-defined]
            e.sort_key_ret = key_ret  # type: ignore[attr-defined]
        else:
            e.sort_key = None  # type: ignore[attr-defined]
            e.sort_key_ret = "int"  # type: ignore[attr-defined]
        if reverse_expr is not None:
            self._check_expr(reverse_expr, scope)
            e.sort_reverse = reverse_expr  # type: ignore[attr-defined]
        else:
            e.sort_reverse = None  # type: ignore[attr-defined]
        e.kwargs = []

    def _check_str_method(self, e: A.MethodCall, scope: Scope) -> None:
        # Methods with non-trivial signatures: split returns list[str]; join
        # consumes a list[str].
        if e.method == "split":
            # str.split([sep[, maxsplit]]). asmpython accepts the optional
            # maxsplit int (front-end); codegen currently ignores it and splits
            # on all occurrences (a full maxsplit lowering is a runtime TODO).
            if len(e.args) > 2:
                raise SemaError("str.split() takes 0 to 2 arguments", e.pos)
            if e.args and A.expr_type(e.args[0]) not in ("str", "any"):
                raise SemaError("str.split() separator must be str", e.pos)
            if len(e.args) == 2 and A.expr_type(e.args[1]) not in ("int", "any"):
                raise SemaError("str.split() maxsplit must be an int", e.pos)
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
        if e.method == "rsplit":
            # str.rsplit(sep, 1): split at the LAST occurrence of sep ->
            # [before, after] (or [s] when absent). Only the maxsplit=1 form is
            # lowered today; other counts need a general right-scan runtime.
            if len(e.args) != 2:
                raise SemaError(
                    "str.rsplit() currently requires exactly (sep, 1)", e.pos
                )
            if A.expr_type(e.args[0]) not in ("str", "any"):
                raise SemaError("str.rsplit() separator must be str", e.pos)
            if not (isinstance(e.args[1], A.IntLit) and e.args[1].value == 1):
                raise SemaError(
                    "str.rsplit() maxsplit must be the literal 1 (only the "
                    "last-separator split is implemented)",
                    e.pos,
                )
            e.inferred_type = "list"
            e.list_el_type = "str"
            return
        if e.method in ("partition", "rpartition"):
            # str.(r)partition(sep) -> (before, sep, after): always a 3-tuple
            # of strings, so the unpack targets type as str (prints / == work).
            if len(e.args) != 1:
                raise SemaError(f"str.{e.method}() takes 1 argument", e.pos)
            if A.expr_type(e.args[0]) not in ("str", "any"):
                raise SemaError(f"str.{e.method}() separator must be str", e.pos)
            e.inferred_type = "tuple"
            e.tuple_elem_types = ["str", "str", "str"]
            return
        if e.method == "join":
            if len(e.args) != 1:
                raise SemaError("str.join() takes 1 argument", e.pos)
            arg_t = A.expr_type(e.args[0])
            if arg_t not in ("list", "any", "int"):
                # "int" is the default type for unannotated vars; accept it
                # leniently so self-hosting code using e.g. `self.lines` passes.
                raise SemaError("str.join() requires list[str]", e.pos)
            if arg_t == "list":
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
                raise SemaError(f"str.{e.method}() takes 0 or 1 argument", e.pos)
            if e.args and A.expr_type(e.args[0]) != "str":
                raise SemaError(f"str.{e.method}() argument must be str", e.pos)
            e.inferred_type = "str"
            return
        if e.method == "zfill":
            if len(e.args) != 1:
                raise SemaError("str.zfill() takes 1 argument", e.pos)
            if A.expr_type(e.args[0]) not in ("int", "any"):
                raise SemaError("str.zfill() argument must be an int", e.pos)
            e.inferred_type = "str"
            return
        if e.method in ("ljust", "rjust", "center"):
            # str.{ljust,rjust,center}(width[, fillchar]); fillchar defaults
            # to a space when omitted.
            if len(e.args) not in (1, 2):
                raise SemaError(f"str.{e.method}() takes 1 or 2 arguments", e.pos)
            if A.expr_type(e.args[0]) not in ("int", "any"):
                raise SemaError(f"str.{e.method}() width must be an int", e.pos)
            if len(e.args) == 2 and A.expr_type(e.args[1]) not in ("str", "any"):
                raise SemaError(f"str.{e.method}() fillchar must be a str", e.pos)
            e.inferred_type = "str"
            return
        if e.method == "format" and isinstance(e.obj, A.StrLit):
            # `"...".format(args)` with a literal format string: codegen lowers
            # this to a concat chain, so the result is a real str.
            for a in e.args:
                self._check_expr(a, scope)
            for _, a in e.kwargs:
                self._check_expr(a, scope)
            kwarg_names = {name for name, _ in e.kwargs}
            for kind, val, _spec, _conv in A.parse_format_fields(e.obj.value):
                if kind != "arg":
                    continue
                if isinstance(val, str):
                    if "." in val or "[" in val:
                        raise SemaError(
                            "str.format() attribute/index access in "
                            f"fields (e.g. '{{0.attr}}', '{{0[0]}}') is not "
                            "supported",
                            e.pos,
                        )
                    if val not in kwarg_names:
                        raise SemaError(
                            f"str.format() got an unexpected field name {val!r}",
                            e.pos,
                        )
                elif val >= len(e.args):
                    raise SemaError(
                        f"str.format() field index {val} out of range "
                        f"({len(e.args)} positional argument(s))",
                        e.pos,
                    )
            e.inferred_type = "str"
            return
        sig = self.STR_METHODS.get(e.method)
        if sig is None:
            # An unmodeled method on a str-typed value. asmpython's
            # `except ... as e` binds the message as a str, but source that uses
            # the caught value as an exception *object* calls methods on it
            # (e.g. `e.format(...)` on a CompileError). Check the args and treat
            # the result as opaque rather than erroring.
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = "any"
            return
        arg_types, ret = sig
        if len(e.args) != len(arg_types):
            raise SemaError(
                f"str.{e.method}() takes {len(arg_types)} argument(s), got {len(e.args)}",
                e.pos,
            )
        _si = 0
        for a, want in zip(e.args, arg_types):
            got = A.expr_type(a)
            if got != want:
                raise SemaError(
                    f"str.{e.method}() argument {_si + 1}: expected {want}, got {got}",
                    e.pos,
                )
            _si = _si + 1
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
        _ffi_i = 0
        for a, want in zip(args, fn.arg_types):
            self._check_expr(a, scope)
            got = A.expr_type(a)
            if got == want:
                _ffi_i = _ffi_i + 1
                continue
            # Allow int -> float promotion.
            if want == "float" and got == "int":
                _ffi_i = _ffi_i + 1
                continue
            # "list_buf": pass a list[int]'s underlying data buffer as a raw
            # pointer (see _gen_ffi_call) -- used for FFI calls that fill a
            # fixed-size struct (e.g. `stat`) the caller reads back as int64
            # words, since string buffers can't survive embedded NUL bytes.
            if want == "list_buf" and got == "list" and getattr(a, "list_el_type", "int") == "int":
                _ffi_i = _ffi_i + 1
                continue
            raise SemaError(
                f"{label}() argument {_ffi_i + 1}: expected {want}, got {got}",
                pos,
            )

    def _maybe_bind_method_args(self, e: A.MethodCall, obj_t: str) -> None:
        """Bind keyword/vararg args on a user-class method call (or super())
        onto positions. No-op for str/list/dict/external methods, which don't
        take keyword args in asmpython's model."""
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

    def _bind_args(
        self,
        e: "A.Call | A.MethodCall",
        names: list,
        defaults: list,
        vararg,
        pos,
        label,
    ) -> None:
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
        # Pre-size the slot list with None placeholders. Built with an explicit
        # loop rather than `[None] * nfixed` so this stays self-compilable
        # (asmpython has no list-repeat operator).
        slots: list = []
        for _ in range(nfixed):
            slots.append(None)
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

    def _expand_starred_args(self, args: list, scope: Scope) -> list:
        """Rewrite `*expr` call arguments in place into one Subscript per
        tuple slot (`expr[0], expr[1], ...`), since asmpython has no runtime
        varargs. Returns the (possibly unchanged) args list."""
        if not any(isinstance(a, A.Starred) for a in args):
            return args
        new_args: list = []
        for a in args:
            if not isinstance(a, A.Starred):
                new_args.append(a)
                continue
            self._check_expr(a.value, scope)
            if not isinstance(a.value, (A.Name, A.Subscript, A.Attr)):
                raise SemaError(
                    "*expr argument unpacking requires a name, subscript, or "
                    "attribute expression (assign the value to a variable "
                    "first)",
                    a.pos,
                )
            ets = self._tuple_elem_types(a.value, scope)
            if not ets:
                raise SemaError(
                    "*expr argument unpacking requires a tuple with known "
                    "element types",
                    a.pos,
                )
            for i in range(len(ets)):
                sub = A.Subscript(
                    obj=a.value, index=A.IntLit(value=i, pos=a.pos), pos=a.pos
                )
                self._check_expr(sub, scope)
                new_args.append(sub)
        return new_args

    def _check_call(self, e: A.Call, scope: Scope) -> None:
        e.args = self._expand_starred_args(e.args, scope)
        if e.func in INTERPRETER_ONLY_BUILTINS:
            raise SemaError(
                f"{e.func}() is not supported: it requires a Python interpreter "
                "and cannot be compiled to native code",
                e.pos,
            )
        if e.func == "range":
            # range(...) as a value materializes a list[int]. (In a `for` header
            # the parser captures range specially and it never becomes a Call.)
            if not (1 <= len(e.args) <= 3):
                raise SemaError(
                    f"range() takes 1-3 arguments, got {len(e.args)}", e.pos
                )
            for a in e.args:
                self._check_expr(a, scope)
                if A.expr_type(a) not in ("int", "any"):
                    raise SemaError("range() arguments must be ints", e.pos)
            e.inferred_type = "list"
            e.list_el_type = "int"
            return
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
            # typed values in asmpython's model.
            if len(e.args) != 2:
                raise SemaError(
                    f"isinstance() takes 2 arguments, got {len(e.args)}", e.pos
                )
            self._check_expr(e.args[0], scope)
            e.inferred_type = "int"
            return
        if e.func == "getattr":
            # getattr(obj, "name"[, default]) -> opaque value. The attribute name
            # must be a string literal (asmpython instances are dicts keyed by
            # field name; a literal lets codegen intern the key). Result is
            # "any" because the field's static type isn't known.
            if not (2 <= len(e.args) <= 3):
                raise SemaError(
                    f"getattr() takes 2-3 arguments, got {len(e.args)}", e.pos
                )
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = "any"
            return
        if e.func == "hasattr":
            # hasattr(obj, "name") -> int 0/1.
            if len(e.args) != 2:
                raise SemaError(
                    f"hasattr() takes 2 arguments, got {len(e.args)}", e.pos
                )
            for a in e.args:
                self._check_expr(a, scope)
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
            # Set the static return type so codegen knows how to interpret it.
            e.inferred_type = {
                "print": "int",
                "len": "int",
                "int": "int",
                "float": "float",
                "str": "str",
                "input": "str",
                "bool": "int",
                "list": "list",
                "tuple": "tuple",
                "dict": "dict",
                "set": "set",
                "frozenset": "set",
                "sum": "int",
                "min": "any",
                "max": "any",
                "abs": "any",
                "round": "float" if len(e.args) >= 2 else "int",
                "pow": "int",
                "sorted": "list",
                "reversed": "list",
                "any": "int",
                "all": "int",
                "ord": "int",
                "chr": "str",
                "repr": "str",
                "type": "any",
                "id": "int",
                "open": "any",
                "vars": "dict",
                "dir": "list",
                "callable": "int",
                "setattr": "int",
                "delattr": "int",
                "iter": "any",
                "next": "any",
                "map": "list",
                "filter": "list",
                "format": "str",
                "hex": "str",
                "oct": "str",
                "bin": "str",
                "divmod": "tuple",
                "hash": "int",
                "issubclass": "int",
                "bytes": "list",
                "bytearray": "list",
            }[e.func]
            if e.func == "abs":
                # abs preserves the operand's numeric type (float -> float so
                # the result prints/operates as a float, not its raw bits).
                e.inferred_type = "float" if A.expr_type(e.args[0]) == "float" else "int"
                return
            if e.func == "type":
                # type(x) -> "<class '...'>" string for any statically-known
                # type (builtin scalar/container, or a user instance); falls
                # back to "any" (the raw RTTI class id) for opaque values.
                arg_t = A.expr_type(e.args[0])
                if arg_t.startswith("instance:") or arg_t in (
                    "int", "float", "str", "list", "dict", "tuple", "set",
                ):
                    e.inferred_type = "str"
                return
            if e.func in (
                "bool",
                "set",
                "frozenset",
                "sum",
                "round",
                "pow",
                "reversed",
                "any",
                "all",
                "ord",
                "chr",
                "repr",
                "id",
            ):
                return
            if e.func in ("min", "max"):
                # min/max: the 1-arg "iterable" form supports key=/reverse=
                # (reverse= is meaningless here but accepted for symmetry with
                # sorted()'s kwarg set — codegen ignores it). The variadic
                # scalar form (min(a, b, ...)) doesn't support key=.
                self._check_sort_kwargs(e, scope)
                if len(e.args) == 1:
                    e.inferred_type = self._list_el_type(e.args[0], scope)
                else:
                    if e.sort_key is not None:
                        raise SemaError(
                            f"{e.func}(): key= is only supported for the "
                            "single-iterable form",
                            e.pos,
                        )
                    types = {A.expr_type(a) for a in e.args}
                    if "float" in types:
                        e.inferred_type = "float"
                    elif "str" in types:
                        e.inferred_type = "str"
                    else:
                        e.inferred_type = "int"
                return
            if e.func == "sorted":
                # sorted(x) -> a new list. Sets/dicts sort their (str) keys;
                # lists/tuples keep their element kind for printing/iteration.
                self._check_sort_kwargs(e, scope)
                t = A.expr_type(e.args[0])
                if t in ("set", "dict"):
                    e.list_el_type = "str"
                else:
                    e.list_el_type = self._list_el_type(e.args[0], scope)
                return
            if e.func == "tuple":
                # tuple(x): a shallow copy in the shared list/tuple layout. The
                # per-slot kinds aren't tracked (source may be any iterable), so
                # downstream indexing/unpacking stays lenient.
                t = A.expr_type(e.args[0])
                if t not in ("list", "tuple", "str", "any", "int"):
                    raise SemaError(
                        "tuple() requires a list, tuple, or string", e.pos
                    )
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
            if e.func == "dict":
                # dict() / dict(other) -> a (shallow-copied) dict. Carry the
                # source's value kind so later reads recover it.
                if e.args:
                    t = A.expr_type(e.args[0])
                    if t not in ("dict", "any"):
                        raise SemaError("dict() requires a dict argument", e.pos)
                    e.value_type = self._dict_value_type(e.args[0], scope)
                return
            if e.func == "divmod":
                # divmod(a, b) -> (a // b, a % b), both ints (floor semantics).
                for a in e.args:
                    t = A.expr_type(a)
                    if t not in ("int", "any"):
                        raise SemaError("divmod() requires int arguments", e.pos)
                e.tuple_elem_types = ["int", "int"]
                return
            # Argument-type sanity for builtins that care. An opaque ("any")
            # argument is accepted everywhere — we can't know its real type.
            if e.func == "len":
                t = A.expr_type(e.args[0])
                if t not in ("str", "list", "dict", "tuple", "set", "any", "int") and not t.startswith("instance:"):
                    # "int" is the default for unannotated vars — accept leniently
                    raise SemaError(
                        "len() requires a string, list, dict, tuple, or set", e.pos
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
                # int/float/str convert directly; list/tuple/dict/set stringify
                # via their repr; an opaque value or an instance (which may define
                # __str__/__repr__) is accepted leniently. All yield a str.
                if t not in (
                    "int", "float", "str", "any", "list", "tuple", "dict", "set"
                ) and not t.startswith("instance:"):
                    raise SemaError(
                        "str() requires a scalar, container, or object", e.pos
                    )
            return
        # Resolve import alias (from mod import orig as local) for bundled-source
        # stdlib functions so type-checking and inference use the real FuncSig.
        if e.func in self.mod.func_aliases and e.func not in self.funcs:
            resolved = self.mod.func_aliases[e.func]
            if resolved in self.funcs:
                e.func = resolved
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
                ty, el, _val = sig.ret_type  # type: ignore
                e.inferred_type = ty
                if ty == "list" and el is not None:
                    e.list_el_type = el
                    if el == "tuple" and sig.ret_list_tuple_types:
                        e.tuple_elem_types = list(sig.ret_list_tuple_types)  # type: ignore
                elif ty == "dict" and _val is not None:
                    # Carry the value kind so `d = f()[k]` / `f()[k].attr`
                    # reads recover it (bare `-> dict` gives value kind "any").
                    e.value_type = _val
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
                if (
                    (e.args or e.kwargs)
                    and not self.classes[e.func].fields
                    and not self._is_exception_class(e.func)
                ):
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
            # A name bound to a lambda: use the lambda's body type so the call
            # result prints/operates correctly (e.g. a str-returning lambda).
            if e.func in self.lambda_rets:
                e.inferred_type = self.lambda_rets[e.func]
            # An opaque-imported callable (bound "any" — e.g. a function pulled
            # in via `from .._runtime.build import build_runtime_shared`) returns
            # an opaque value, as does a capitalized name (conventionally an
            # imported class/constructor: `Path(...)`, `Token(...)`). Either way
            # the result is "any" so attribute/method access stays lenient.
            # Anything else falls back to int.
            elif scope.types.get(e.func) == "any" or e.func[:1].isupper():
                e.inferred_type = "any"
            else:
                e.inferred_type = "int"
            return
        raise SemaError(f"undefined function {e.func!r}", e.pos)


def analyze(mod: A.Module, *, source_dir=None) -> None:
    """Run semantic analysis over `mod`.

    `source_dir` is the directory of the source file (a Path or None). It seeds
    the search path for `include("pkg")` so a `<pkg>.asmpkg` next to the source
    is found. None disables file-relative package resolution (used by the
    self-host gauntlet and tests that analyse in isolation).
    """
    SemaAnalyzer(mod, source_dir=source_dir).analyze()
