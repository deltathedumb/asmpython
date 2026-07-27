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

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Optional

from . import ast_nodes as A
from .. import stdlib
from ..stdlib import STDLIB_BINDINGS
from .errors import ErrorCode, SemaError


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
        raise SemaError("Assembly: operand must not be an empty string", pos, ErrorCode.E_ASM_OPERAND)

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
            f"Assembly: {val!r} is not a recognised x86-64 register", pos,
            ErrorCode.E_ASM_REGISTER,
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
    # The three builtin descriptor wrappers. Each wraps one callable
    # (property's is optional -- a bare `property()` placeholder). ir_lower
    # builds a tagged cell isinstance()/`.__func__`/`.fget` can inspect; the
    # result is opaque ("any") to the rest of the type system.
    "staticmethod": (1, 1),
    "classmethod": (1, 1),
    "property": (0, 1),
    # slice(stop) / slice(start, stop) / slice(start, stop, step) -- a
    # runtime slice object usable as a dynamic subscript index.
    "slice": (1, 3),
    "reversed": (1, 1),
    "any": (1, 1),
    "all": (1, 1),
    "ord": (1, 1),
    "chr": (1, 1),
    "bitcast_f2i": (1, 1),  # bitcast_f2i(x: float) -> int: raw IEEE-754 bit pattern, not a numeric conversion
    "bitcast_i2f": (1, 1),  # bitcast_i2f(x: int) -> float: reverse of bitcast_f2i (bit pattern, not a numeric conversion)
    "repr": (1, 1),
    "ascii": (1, 1),
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
    "zip": (2, 64),    # zip(*iterables) -> iterator of tuples
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

# Builtins that are accepted as values as well as in direct call position.
# Some are lowered by dedicated syntax paths rather than the BUILTINS table.
BUILTIN_VALUE_NAMES: frozenset[str] = frozenset(BUILTINS) | frozenset({
    "range",
    "object",
    "slice",
    "property",
    "classmethod",
    "staticmethod",
    "enumerate",
    "isinstance",
    "hasattr",
    "getattr",
    "ascii",
})


# Builtin exception classes. asmpython's exception runtime is string-message
# based, but the *front end* must accept idiomatic `raise ValueError(msg)` and
# bare `raise NotImplementedError`. These names resolve as class objects and,
# when called, yield an (external) instance.
BUILTIN_EXCEPTIONS: frozenset[str] = frozenset({
    "BaseException",
    "Exception",
    "SystemExit",
    "KeyboardInterrupt",
    "GeneratorExit",
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
    "StopAsyncIteration",
    "ArithmeticError",
    "ZeroDivisionError",
    "OverflowError",
    "FloatingPointError",
    "AssertionError",
    "ImportError",
    "ModuleNotFoundError",
    "OSError",
    "IOError",
    "FileNotFoundError",
    "BlockingIOError",
    "ChildProcessError",
    "ConnectionError",
    "BrokenPipeError",
    "ConnectionAbortedError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "InterruptedError",
    "IsADirectoryError",
    "NotADirectoryError",
    "PermissionError",
    "ProcessLookupError",
    "TimeoutError",
    "BufferError",
    "EOFError",
    "MemoryError",
    "ReferenceError",
    "SystemError",
    "UnboundLocalError",
    "UnicodeError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeTranslateError",
    "SyntaxError",
    "IndentationError",
    "TabError",
})

# Builtin scalar/container type names usable as a bare *value* (not just a
# call target or an annotation), e.g. `{"type": str}` mimicking argparse's
# `add_argument(type=str)` convention. asmpython has no first-class type
# objects -- like a user class or builtin exception used as a value, this
# loads a stable per-name RTTI id the program never actually inspects (see
# codegen.py's BUILTIN_TYPE_IDS and class_ids).
BUILTIN_TYPE_NAMES: frozenset[str] = frozenset({
    "int", "float", "str", "bool", "list", "dict", "tuple", "set",
})


# Static types accepted as a dict KEY. asmpython's dict runtime is string-keyed;
# ir_lower's `_lower_dict_key` encodes every one of these to a canonical string
# (str used directly, int to its decimal spelling, everything else to its
# `repr()`) so a value-equal key always maps to the same slot. Mirrors Python's
# own rule that keys must be hashable-by-value: the immutable scalar/tuple kinds
# qualify, the mutable containers (list/dict/set) do not. "any" is the untracked
# opaque key (lenient -- it's a real string pointer at runtime); an `instance:*`
# key is likewise accepted (a user object encodes via its repr).
def _is_dict_key_type(ty: str) -> bool:
    return (
        ty in ("str", "any", "int", "bool", "float", "tuple")
        or ty.startswith("instance:")
    )


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

# Unary operator → dunder method name for instance dispatch.
DUNDER_UNARY: dict[str, str] = {
    "-": "__neg__",
    "+": "__pos__",
    "~": "__invert__",
    "abs": "__abs__",
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
_dunder_same = set()
for fwd, _rfl in DUNDER_BINOP.values():
    _dunder_same.add(fwd)
for _fwd, rfl in DUNDER_BINOP.values():
    _dunder_same.add(rfl)
_dunder_same |= {"__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__"}
DUNDER_SAME_TYPE_OTHER: frozenset[str] = frozenset(_dunder_same)

# str method registry split into two flat dicts so self-compiled sema can
# use them without len()-on-nested-tuple issues (see Analyzer.STR_METHODS
# comment for the full explanation).  _STR_METHOD_ARGC stores the expected
# argument count (int) and _STR_METHOD_RET stores the return type (str).
# All str methods that accept arguments expect str-typed values.
_STR_METHOD_ARGC: dict = {
    "upper": 0, "lower": 0, "casefold": 0, "capitalize": 0, "swapcase": 0,
    "title": 0, "strip": 0, "lstrip": 0, "rstrip": 0,
    "startswith": 1, "endswith": 1,
    "removeprefix": 1, "removesuffix": 1, "count": 1,
    "replace": 2, "translate": 1,
    "isdigit": 0, "isalpha": 0, "isalnum": 0, "isspace": 0,
    "isupper": 0, "islower": 0, "isidentifier": 0,
    "isnumeric": 0, "isprintable": 0,
}
_STR_METHOD_RET: dict = {
    "upper": "str", "lower": "str", "casefold": "str", "capitalize": "str",
    "swapcase": "str", "title": "str", "strip": "str", "lstrip": "str",
    "rstrip": "str",
    "startswith": "int", "endswith": "int",
    "removeprefix": "str", "removesuffix": "str", "count": "int",
    "replace": "str", "translate": "str",
    "isdigit": "int", "isalpha": "int", "isalnum": "int", "isspace": "int",
    "isupper": "int", "islower": "int", "isidentifier": "int",
    "isnumeric": "int", "isprintable": "int",
}


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
    # When ret_type is ("list", "list"/"dict", ...) from a `-> list[list[T]]`
    # or `-> list[dict[K,V]]` annotation, the inner element/value kind ("str",
    # "int", etc.) — else None. Lets call sites stamp el_value_type so
    # `for row in rows: row[i]` recovers the leaf type.
    ret_inner_el_type: object = None
    # Parameter names and their default expressions (parallel to params,
    # including `self` for methods). Used to bind keyword arguments onto
    # positions at call sites.
    param_names: list = field(default_factory=list)
    param_defaults: list = field(default_factory=list)
    # `overload` extension: resolved per-parameter static types, parallel to
    # `param_names` ("int"/"str"/"float"/"any" per slot, "any" for an
    # unannotated/uninferrable parameter). Populated at registration time
    # from the same annotation resolution every other FuncSig field already
    # uses -- previously computed and discarded, never stored, since
    # ordinary (non-overloaded) call resolution only ever needed arity, not
    # per-parameter types. Needed here because overload dispatch has to
    # pick the best-matching signature by argument type, not just count.
    param_types: list = field(default_factory=list)
    # Name of the `*args` parameter (the trailing list slot), or None.
    vararg: Optional[str] = None
    # Name of the `**kwargs` parameter (the trailing dict slot), or None.
    kwarg: Optional[str] = None
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
    # True when the function has an explicit `-> bool` annotation (so call
    # sites can render the return value as True/False in print/str/f-string).
    ret_bool: bool = False


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
    # Fields whose annotation was `bool`. asmpython has no bool type -- bool IS
    # int -- so this only decides RENDERING: `print(cfg.debug)` writes
    # True/False rather than 1/0. Mirrors `FuncSig.ret_bool` and `Scope`'s own
    # `bool_flags` for locals.
    field_bools: set = field(default_factory=set)
    # Companion collection-shape info for fields, so `self.xs[i]`, `for x in
    # self.xs`, and nested reads like `self.rows[i]["k"]` recover the same
    # metadata locals carry in Scope. `field_el_types` holds the outer list
    # element kind or dict value kind. `field_inner_value_types` carries one
    # more nesting level for list[list[T]] / list[dict[K,V]] / dict[str,
    # list[T]] / dict[str, dict[K,V]], `field_value_tuple_types` the per-slot
    # kinds for list[tuple[...]] / dict[str, tuple[...]], and
    # `field_tuple_types` the per-slot kinds of a field whose own top-level
    # type is tuple.
    field_el_types: dict[str, str] = field(default_factory=dict)
    field_inner_value_types: dict[str, str] = field(default_factory=dict)
    field_value_tuple_types: dict[str, list] = field(default_factory=dict)
    field_tuple_types: dict[str, list] = field(default_factory=dict)
    # Wave-1 extensions (access/final/sealed): method/field name -> modifier
    # state, populated during signature collection regardless of whether the
    # owning extension is active (cheap to always record; enforcement itself
    # is what's gated on _ext_active). "public" is the implicit default for
    # anything absent from `access`/`field_access`.
    access: dict[str, str] = field(default_factory=dict)
    field_access: dict[str, str] = field(default_factory=dict)
    is_final: bool = False
    final_methods: set = field(default_factory=set)
    is_sealed: bool = False
    sealed_permits: list = field(default_factory=list)
    # `immutable` extension: whole-class (@immutable on the class) or a set
    # of specific field names (@immutable on individual class-body fields).
    # Either form only allows writes from inside the declaring class's own
    # __init__ (self.in_function == f"{name}__init__").
    is_immutable: bool = False
    immutable_fields: set = field(default_factory=set)


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
    # For names typed "outparam" (an exported function's raw-pointer-out
    # parameter -- see ast_nodes/parser's `outparam[T]` annotation), the
    # pointee kind ("int"/"float") the store-through assignment must match.
    outparam_el_types: dict[str, str] = field(default_factory=dict)
    # For names typed "inparam" (an exported function's raw caller-owned
    # ARRAY parameter -- see `inparam[T]`), the element kind read out by
    # `items[i]`.
    inparam_el_types: dict[str, str] = field(default_factory=dict)
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
        if ty == "outparam" and el_type is not None:
            self.outparam_el_types[name] = el_type
        if ty == "inparam" and el_type is not None:
            self.inparam_el_types[name] = el_type

    def __contains__(self, name: str) -> bool:
        return name in self.types


# `overload` extension: single canonical symbol-mangling scheme, called
# from every resolved-overload-call site (sema, and both codegen backends'
# call-emission, which read A.Call.resolved_overload_symbol back rather
# than re-deriving it) and from the function-definition-emission side
# (codegen/ir_lower, for the actual compiled symbol each @overload def
# gets). Suffix is arity plus a short type tag per parameter (i=int,
# f=float, s=str, a=any) -- enough to disambiguate same-arity overloads
# differing only by parameter type, without a full serialized-signature
# mangling scheme this wave's 6 real configs don't need.
_OVERLOAD_TYPE_TAG = {"int": "i", "float": "f", "str": "s"}


def _overload_symbol(name: str, sig) -> str:
    tags = "".join(_OVERLOAD_TYPE_TAG.get(t, "a") for t in sig.param_types)
    return f"{name}__ov{sig.arity}{tags}"


def _syntactic_reachable_names(mod: A.Module) -> "tuple[set, set]":
    """Pre-sema call-graph walk: which top-level functions and class methods
    are reachable from the module's real entry point (`mod.body`)?

    Deliberately cheaper and less precise than `ir_lower.py`'s
    `_reachable_callables` -- that one runs AFTER sema and can key off
    sema-populated fields (`A.expr_type()`'s `.inferred_type`,
    `resolved_overload_symbol`, `dunder_call_owner`, etc.) to resolve exactly
    which class a method call dispatches to. This walker runs BEFORE sema
    (it has to: it's used to decide whether sema may safely skip a broken
    body), so it only has the plain-string AST fields the parser already
    populated: `A.Call.func`, `A.MethodCall.method`, bare `A.Name` refs. It
    can't tell which class a `MethodCall.method` call targets, so it
    conservatively marks EVERY class method matching that bare name reachable
    across all classes -- an over-approximation that only ever marks too
    much, never too little, which is the safe direction for this walker's
    one job (deciding what's safe to skip if it errors).
    """
    method_defs: dict = {}
    methods_by_name: dict = {}
    for cls in mod.classes:
        for m in cls.methods:
            method_defs[(cls.name, m.name)] = m
            methods_by_name.setdefault(m.name, []).append(cls.name)
    func_defs = {f.name: f for f in mod.funcs}

    needed_funcs: set = set()
    needed_methods: set = set()
    func_queue: list = []
    method_queue: list = []

    def add_func(name) -> None:
        if isinstance(name, str) and name in func_defs and name not in needed_funcs:
            needed_funcs.add(name)
            func_queue.append(name)

    def add_method_by_name(name) -> None:
        if not isinstance(name, str):
            return
        for owner in methods_by_name.get(name, []):
            key = (owner, name)
            if key not in needed_methods:
                needed_methods.add(key)
                method_queue.append(key)

    def visit(node) -> None:
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, A.MethodCall):
            add_method_by_name(node.method)
        elif isinstance(node, A.Call):
            add_func(node.func)
            if node.func in {c.name for c in mod.classes}:
                add_method_by_name("__init__")
        elif isinstance(node, A.Name):
            add_func(node.name)
            add_method_by_name(node.name)
        if not is_dataclass(node):
            return
        if isinstance(node, (A.MethodCall, A.Attr)) and isinstance(node.obj, A.Name):
            skip_field = "obj"
        else:
            skip_field = None
        for fld in fields(node):
            if fld.name == "pos" or fld.name == skip_field:
                continue
            value = getattr(node, fld.name)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, tuple):
                        for sub in item:
                            visit(sub)
                    else:
                        visit(item)
            elif isinstance(value, tuple):
                for sub in value:
                    visit(sub)
            else:
                visit(value)

    for st in mod.body:
        visit(st)
    # `main` and every native-library export (`@access(Public)`/`@abi(...)`)
    # are never called from anything in mod.body -- `main` runs implicitly
    # as the process entry point (absent an explicit `if __name__ ==
    # "__main__": main()` guard), and an export is only ever called from
    # OUTSIDE the compiled program. Without this, this walker's caller
    # (_analyze_with_unreachable_project_tolerance) marks them "unreachable
    # project code" and sets is_stdlib=True on them purely to borrow sema's
    # stdlib-tolerance path -- which SILENTLY DISCARDS every real error in
    # their bodies (see _try_check_block's tolerate=True branch), not just
    # relaxes checking. Confirmed via a real regression: an outparam[T]
    # write-through violation in an exported function's body was accepted
    # instead of raising, because reaching this exact silent-discard path.
    # ir_lower.py's OWN _reachable_callables (a similar, separate walker
    # that runs after sema) needed the identical fix for the identical
    # reason -- this is the third such reachability walker in this
    # compiler needing "main"/exports seeded as roots.
    if "main" in func_defs:
        add_func("main")
    for f in mod.funcs:
        if getattr(f, "is_public_export", False):
            add_func(f.name)
    for cls in mod.classes:
        class_public = getattr(cls, "is_public_export", False)
        for m in cls.methods:
            if class_public or getattr(m, "is_public_export", False):
                key = (cls.name, m.name)
                if key not in needed_methods:
                    needed_methods.add(key)
                    method_queue.append(key)

    while func_queue or method_queue:
        while func_queue:
            name = func_queue.pop(0)
            f = func_defs.get(name)
            if f is not None:
                for st in f.body:
                    visit(st)
        while method_queue:
            key = method_queue.pop(0)
            m = method_defs.get(key)
            if m is not None:
                for st in m.body:
                    visit(st)

    return needed_funcs, needed_methods


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
        b = STDLIB_BINDINGS[key]
        return b
    raise SemaError(f"no such module: {name!r}", code=ErrorCode.E_NO_SUCH_MODULE)


class SemaAnalyzer:
    def __init__(
        self,
        mod: A.Module,
        *,
        source_dir=None,
        collect_errors: bool = False,
        active_extensions: "frozenset[str] | None" = None,
    ) -> None:
        self.mod = mod
        self.source_dir = source_dir
        self.collect_errors = collect_errors
        self._collected_errors: list = []
        # Compiler-extension activation state, threaded down from the CLI's
        # `--ext` flags (mirrors `Parser.ext_ctx.is_active`, but sema has no
        # ExtensionContext of its own -- extensions that need sema-level
        # enforcement (as opposed to pure new syntax) check this set instead).
        self.active_extensions: frozenset = active_extensions or frozenset()
        self.funcs: dict[str, FuncSig] = {}
        self.classes: dict[str, ClassSig] = {}
        # Every class name in the module, known from the moment sema starts.
        # `self.classes` (full signatures) is only filled in during the class
        # pass, which runs AFTER function signatures are built -- so
        # `_resolve_annot` could not recognize a user class by name while
        # resolving `def make() -> Factory:`, and silently fell through to the
        # int default. That mistyped the RESULT of every factory function in
        # the language, and made a `p: SomeClass` parameter resolve to an
        # opaque external instance instead of the real class (losing its
        # methods). A name-only set, seeded up front, is what makes annotation
        # resolution independent of pass order.
        self._class_name_set: set[str] = set()
        # `enum` extension: enum type name -> {member name -> resolved int
        # value}. Pure sema-side bookkeeping -- `Color.RED` folds to a plain
        # IntLit at the read site, so this table exists only to let
        # `Color.RED == Direction.NORTH`-style cross-enum comparisons be
        # flagged as a type error even though both sides are plain ints by
        # the time codegen would see them.
        self.enum_types: dict[str, dict[str, int]] = {}
        # `interface` extension: interface name -> {method name -> stub
        # FuncSig}. Built before class-signature collection so
        # `class X(interface=Name):` conformance-checking has the interface's
        # method table ready when it processes each class.
        self.interface_methods: dict = {}
        # `overload` extension: name -> list[FuncSig], populated ONLY for
        # names where every same-named def/method is @overload-marked (a
        # deliberately additive, parallel structure -- self.funcs/
        # ClassSig.methods stay single-FuncSig-per-name for the ordinary,
        # non-overloaded case, unchanged; call-resolution sites check
        # overload_sets FIRST and only fall through to the existing
        # single-lookup logic when the name isn't in it, minimizing the
        # blast radius across the ~10 call-resolution sites that assume
        # one signature per name).
        self.overload_sets: dict = {}
        self.method_overload_sets: dict = {}  # (class_name, method) -> list[FuncSig]
        # Variable name -> return type of the lambda bound to it, so an indirect
        # call `f(...)` on a name-bound lambda gets the right result type.
        self.lambda_rets: dict[str, str] = {}
        # Closure factory name -> the lifted function its returned closure
        # wraps, and variable name -> that same function for a variable a
        # factory's result was assigned to. A call through such a variable binds
        # against the target's real signature, which is the only way a target
        # declaring `*args` gets its arguments packed.
        self._closure_targets: dict[str, str] = {}
        self._closure_factories: set = set()
        self._closure_var_targets: dict[str, str] = {}
        # Local names EXPLICITLY declared `dict[str, object]` (a genuinely
        # heterogeneous dict). Their value kind must stay "any" -- unlike a
        # bare `dict`/`{}`, a later single-kind write must NOT narrow it to
        # that one kind (the user said the values vary). Keeping it "any" makes
        # ir_lower box each scalar written in and unbox each read out, so
        # `type(d[k])`/`isinstance(d[k], T)` answer correctly per key.
        self._explicit_object_dicts: set[str] = set()
        # Same idea for a `list[object]` local: a genuinely heterogeneous list
        # whose scalar elements must be boxed on the way in so `type(xs[i])`
        # answers, and which a bare `list` (element kind unknown, left raw for
        # existing homogeneous-list code) must NOT be confused with.
        self._explicit_object_lists: set[str] = set()
        # Module-level names (imports + top-level assignments). Populated by
        # analyze() before function/method bodies are checked.
        self.global_scope: Scope = Scope()
        # Names ever declared `const` (via the `constants` compiler
        # extension), mapped to their declaration's SourcePos. This is a
        # SEPARATE side-table rather than something tracked through `Scope`,
        # because `Scope` is flat and function-local scopes are seeded by
        # COPYING module globals (`_seed_globals_into`) rather than chaining
        # to a parent -- so `name in scope.types` inside a function body
        # cannot distinguish "this is the module-level const" from "an
        # ordinary function-local that happens to share the name after
        # seeding". Once a name is recorded here it is locked forever for
        # the rest of the module.
        self.const_names: dict = {}
        # `readonly_params`/`const_params` extensions: names locked against
        # reassignment for the currently-checked function/method body only
        # -- rebuilt fresh per function (see the per-function/method
        # body-check loops), unlike const_names' forever-module-wide lock.
        self._locked_params: set = set()
        # `no_global_mutation` extension: function name -> set of names it
        # declared via `global` -- populated by A.Global's handler (see
        # _check_stmt), consulted by _require_assignable's callers.
        self._globals_declared_in: dict = {}
        # `no_shadowing` extension: the currently-checked lifted function's
        # own captured free-variable names, so a NEW body-local binding of
        # the same name can be flagged (see _check_no_shadowing_free_var).
        self._current_free_vars: set = set()
        # `no_shadowing` extension: names already bound at least once
        # within the CURRENTLY-checked function body -- a real "have we
        # seen a first bind of this name yet" tracker. `scope.types` can't
        # serve this purpose: it's pre-seeded with every module global by
        # _seed_globals_into before any body statement runs, so `target not
        # in scope.types` is always false for a name that's also a global,
        # exactly the case case (b) needs to detect. Reset at the start of
        # each function/method body check.
        self._locally_bound: set = set()
        self.loop_depth = 0
        self.in_function: Optional[str] = None
        self.in_lifted: bool = False  # True when checking a lifted nested func
        # Name of the class whose method body is currently being checked, so
        # `super()` can resolve against its base. None outside a method.
        self.current_class: Optional[str] = None
        # Name of the `cls` parameter inside a @classmethod body (e.g. "cls").
        # Used to rewrite `cls.field` → `ClassName.field` so the existing
        # class-var read/write codegen path handles it instead of null-ptr deref.
        self.classmethod_cls_param: Optional[str] = None
        # Imported FFI: bindings either bound under a module prefix or
        # lifted directly into the namespace via from-import.
        self.imported_modules: dict[str, dict] = {}
        self.ffi_funcs: dict[str, stdlib.Func] = {}
        self.ffi_consts: dict[str, stdlib.Const] = {}
        # asmpython.mlang support: uid (str(id(assign_stmt))) -> the
        # mlang_support.MlangResult's `funcs` dict for that Code(...)
        # literal, plus the compiled object bytes each such uid maps to
        # (mlang_objects), consumed by driver.py's link step. Populated by
        # _inject_mlang_if_needed, consulted by A.MethodCall's `mlang:`
        # dispatch above.
        self.mlang_code_funcs: dict[str, dict] = {}
        self.mlang_objects: "list[tuple[bytes, str]]" = []
        self._tuple_scan_globals: Scope = Scope()
        # name -> per-slot element kinds for functions that return a tuple
        # (i.e. have a `return a, b` somewhere). Lets `q, r = f()` recover
        # the per-target types at the call site. Computed in analyze().
        self.func_ret_tuple: dict[str, list[str]] = {}
        # (qualified_name, param_index) -> (ty, el, val, tup) for parameters
        # with no annotation and no default, inferred from literal-typed
        # arguments at call sites. `qualified_name` is a function's plain name,
        # or "ClassName.method_name" for a method. See
        # `_infer_unannotated_params`.
        self.inferred_param_types: dict[str, tuple] = {}
        # func_name -> list of (ty, el_type, val_type) for each free variable,
        # populated by _prescan_fv_types() before the main analysis loops.
        self._fv_types: dict = {}

    def _ensure_synthetic_func(self, fdef: A.FuncDef, ret_ty: str = "int") -> None:
        if fdef.name not in self.funcs:
            self.funcs[fdef.name] = FuncSig(
                name=fdef.name,
                arity=len(fdef.params),
                n_defaults=0,
                pos=fdef.pos,
                ret_type=(ret_ty, None, None),
                param_names=list(fdef.params),
                param_defaults=[None] * len(fdef.params),
                # Mirrors every ordinary def-based FuncSig's own
                # `vararg=f.vararg` (see the main function-registration
                # pass) -- fdef.vararg's name is already included in
                # fdef.params (same convention _parse_funcdef's own vararg
                # parsing uses), so `arity` above already counts it; this
                # just lets call-site binding know which trailing param
                # absorbs surplus positional args, instead of treating it
                # as an ordinary required parameter. Needed so a lambda's
                # synthesized FuncDef (the only caller that currently ever
                # sets fdef.vararg) gets real vararg-packing behavior
                # identical to a normal `def f(*args): ...` -- there was
                # previously no way for a synthesized function to have a
                # vararg at all.
                vararg=fdef.vararg,
            )
        if not any(f.name == fdef.name for f in self.mod.funcs):
            self.mod.funcs.append(fdef)

    def _ensure_builtin_value_func(self, name: str, pos: A.SourcePos) -> str:
        fname = f"__builtin_value_{name}"
        if fname in self.funcs:
            return fname
        a_name = A.Name(name="a", pos=pos)
        b_name = A.Name(name="b", pos=pos)
        cmp = A.Compare(ops=["<" if name == "min" else ">"], operands=[a_name, b_name], pos=pos)
        body = A.IfExp(test=cmp, body=a_name, orelse=b_name, pos=pos)
        fdef = A.FuncDef(
            name=fname,
            params=["a", "b"],
            body=[A.Return(value=body, pos=pos)],
            pos=pos,
            param_types=[("int", None), ("int", None)],
            ret_type=("int", None),
        )
        self._ensure_synthetic_func(fdef, "int")
        return fname

    def _resolve_class_chain(self, name: str) -> list:
        """[name, parent, grandparent, ...] for a user-defined class."""
        out: list[str] = []
        cur = name
        while cur is not None and cur not in out:
            out.append(cur)
            cls: ClassSig = self.classes.get(cur)
            cur = cls.parent if cls is not None else None
        return out

    def _common_class_ancestor(self, a: str, b: str) -> "str | None":
        """Nearest common ancestor of two user-defined classes, e.g. for
        `WindowsCodegen` and `Freestanding16Codegen` both descending from
        `Codegen`. Returns None if they share no modeled ancestor (siblings
        with only an external/unmodeled base, or unrelated classes)."""
        chain_a = self._resolve_class_chain(a)
        chain_b = set(self._resolve_class_chain(b))
        for cls_name in chain_a:
            if cls_name in chain_b:
                return cls_name
        return None

    def _has_external_base(self, class_name: str) -> bool:
        """True if `class_name` or any ancestor inherits from a base that isn't
        a user-defined class (a builtin like Exception, or a name imported from
        another module). Such a base may supply methods/fields asmpython can't
        see, so member access against it is checked leniently."""
        cur = class_name
        seen: set = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls: ClassSig = self.classes.get(cur)
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
        classes = {c.name: c for c in self.mod.classes}
        current = class_name
        seen: set[str] = set()
        while current is not None and current not in seen:
            seen.add(current)
            c = classes.get(current)
            if c is None:
                break
            if getattr(c, "is_dataclass", False):
                current = c.parent
                continue
            for cv in getattr(c, "class_vars", []) or []:
                cvname, _annot, cvdefault = cv
                if cvname == var and cvdefault is not None:
                    lit = self._literal_arg_type(cvdefault)
                    if lit is not None:
                        return lit[0]
                    return self._static_value_info(cvdefault, {})[0]
            current = c.parent
        return None

    def _class_var_overridden_in_subclass(self, class_name: str, var: str) -> bool:
        """True if any *descendant* of `class_name` redeclares class var `var`.

        A `cls.<var>` read inside an inherited @classmethod must resolve to the
        RUNTIME class's override, not the statically-compiled owner's value --
        so when a subclass shadows `var`, the `cls.<var> -> Owner.<var>` static
        rewrite must be suppressed, leaving `cls.<var>` intact for the runtime
        class-id dispatch (dynamic_classvar_compat_fixes) to resolve per class.
        Without this guard, `Server`/`Client` overriding `realms` all read
        `Base.realms` through the single shared `Base__method` symbol."""
        classes = {c.name: c for c in self.mod.classes}
        for c in self.mod.classes:
            if c.name == class_name:
                continue
            # Is c a descendant of class_name?
            cur = c.parent
            seen: set[str] = set()
            descends = False
            while cur is not None and cur not in seen:
                seen.add(cur)
                if cur == class_name:
                    descends = True
                    break
                parent_cls = classes.get(cur)
                cur = parent_cls.parent if parent_cls is not None else None
            if not descends:
                continue
            for cv in getattr(c, "class_vars", []) or []:
                if cv[0] == var:
                    return True
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
            # Explicit ClassSig annotation: self.classes.get(cur)'s result
            # type defaulted wrong (unannotated dict.get), so `cls.methods`
            # read as opaque "any" instead of the real dict -- a 5th
            # instance of the recurring opaque-value bug class this
            # session, this time corrupting a dict LOOKUP (not a list
            # index/len), surfaced as a hard segfault inside
            # _runtime_dict_lookup_slot the moment a real constructor/
            # method-resolution call exercised this function for the
            # first time (it's never reached compiling a program with no
            # classes, which is why test_kwargs_func.py passed while
            # test_kwargs_init.py crashed).
            cls: ClassSig = self.classes.get(cur)
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
            cls: ClassSig = self.classes.get(cur)
            if cls is None:
                return None
            if prop_name in cls.setters:
                return cls.setters[prop_name]
            cur = cls.parent
        return None

    def _check_exc_type_name(self, name: str, pos, scope: "Scope | None" = None) -> None:
        """Validate that `name` (from an `except <name>:` clause) refers to a
        builtin exception or a user class deriving from one."""
        if name in BUILTIN_EXCEPTIONS:
            return
        if name in self.classes and self._is_exception_class(name):
            return
        if name not in self.classes:
            # Not a locally-defined class. If it's in scope (imported from
            # another module), accept leniently — we can't verify the
            # hierarchy at sema time. If it's not in scope at all, it's a
            # genuinely unknown name and we reject it.
            if scope is None or name in scope.types:
                return
        raise SemaError(
            f"'{name}' is not an exception type", pos,
            ErrorCode.E_NOT_AN_EXCEPTION,
        )

    def _is_interpreter_only_method(self, obj_name: str, method: str) -> bool:
        """True if `obj_name.method(...)` is one of INTERPRETER_ONLY_METHODS.

        Written as explicit string comparisons rather than
        `(obj_name, method) in INTERPRETER_ONLY_METHODS` because the latter
        is a tuple-in-frozenset-of-tuples membership test, and codegen's
        set/dict membership lowering only supports str/int-keyed
        sets/dicts (see _gen_dict_in) -- a tuple needle isn't converted and
        is used as a raw pointer "key" under self-compilation, which never
        reliably matches. (Also avoids `for a, b in frozenset_of_tuples:`,
        a tuple-unpacking for-loop over a dict-backed container, which
        _gen_for_dict doesn't support either -- it expects a single bind
        var, not unpack targets.)
        """
        for pair in INTERPRETER_ONLY_METHODS:
            if obj_name == pair[0] and method == pair[1]:
                return True
        return False

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
            cls: ClassSig = self.classes.get(cur)
            if cls is None:
                # Parent isn't a user class; it's an exception only if its name
                # is a builtin exception (checked at the top of the next loop).
                return cur in BUILTIN_EXCEPTIONS
            cur = cls.parent
        return False

    def _class_descends_from(self, class_name: Optional[str], ancestor: str) -> bool:
        """True if `class_name` is `ancestor` or a (possibly indirect) subclass."""
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            if cur == ancestor:
                return True
            seen.add(cur)
            cls: ClassSig = self.classes.get(cur)
            cur = cls.parent if cls is not None else None
        return False

    def _check_access(
        self, owner_cls: str, member_name: str, is_field: bool, pos
    ) -> None:
        """Enforce the `access` extension's @private/@protected modifiers.

        `owner_cls` is the class the read/call/assignment was resolved
        against (e.g. `A.expr_type(e.obj)`'s "instance:<Class>"); walks the
        parent chain to find which class in that chain actually *declares*
        `member_name` (mirrors `_resolve_field_type`'s own walk) so a
        modifier declared on a base class is enforced against subclass
        instances too. No-op if the extension isn't active, the member
        isn't found (some other check reports that), or it's public
        (absent from the access dict is the implicit public default).
        """
        if not self._ext_active("access"):
            return
        cur = owner_cls
        seen = set()
        declaring_cls = None
        level = None
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls: ClassSig = self.classes.get(cur)
            if cls is None:
                return
            table = cls.field_access if is_field else cls.access
            if member_name in table:
                declaring_cls, level = cur, table[member_name]
                break
            cur = cls.parent
        if level is None or level == "public":
            return
        if level == "private":
            if self.current_class != declaring_cls:
                raise SemaError(
                    f"{declaring_cls}.{member_name!r} is private and can only "
                    f"be accessed from {declaring_cls}'s own methods",
                    pos,
                    ErrorCode.E_PRIVATE_ACCESS_VIOLATION,
                )
        elif level == "protected":
            if self.current_class is None or not self._class_descends_from(
                self.current_class, declaring_cls
            ):
                raise SemaError(
                    f"{declaring_cls}.{member_name!r} is protected and can only "
                    f"be accessed from {declaring_cls} or one of its subclasses",
                    pos,
                    ErrorCode.E_PROTECTED_ACCESS_VIOLATION,
                )

    def _check_immutable(self, owner_cls: str, field_name: str, pos) -> None:
        """Enforce the `immutable` extension: writes to a whole-class-
        @immutable class or an individually-@immutable field are only
        allowed from inside the declaring class's own __init__.

        Walks the parent chain like `_check_access` to find which class
        actually declares the field-or-class-level immutability, so a base
        class's @immutable still protects fields written through a
        subclass instance.
        """
        if not self._ext_active("immutable"):
            return
        cur = owner_cls
        seen = set()
        declaring_cls = None
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls: ClassSig = self.classes.get(cur)
            if cls is None:
                return
            if cls.is_immutable or field_name in cls.immutable_fields:
                declaring_cls = cur
                break
            cur = cls.parent
        if declaring_cls is None:
            return
        if self.in_function != f"{declaring_cls}____init__":
            raise SemaError(
                f"cannot assign to {declaring_cls}.{field_name!r}: it is "
                f"immutable outside {declaring_cls}.__init__",
                pos,
                ErrorCode.E_IMMUTABLE_FIELD_REASSIGNED,
            )

    def _try_field_callable_call(self, e, class_name: str, scope: Scope) -> bool:
        """`obj.fn(args)` where `fn` is a FIELD holding a callable value, not a
        method: `self.fn = fn` in __init__, then `self.fn(x)`.

        The parser can't tell the two apart (both are `obj.name(...)`), and
        method resolution rightly fails -- but the field's own type says
        `callable:<ret>`, so this is an ordinary indirect call through that
        field's slot. Stamped rather than rewritten in place because a
        `_check_expr` case can't replace the node in its parent; ir_lower reads
        `field_callable` and lowers the field read as the call target.
        """
        ftype = self._resolve_field_type(class_name, e.method)
        if not (isinstance(ftype, str) and ftype.startswith("callable:")):
            return False
        for a in e.args:
            self._check_expr(a, scope)
        for _kn, _kv in getattr(e, "kwargs", []) or []:
            self._check_expr(_kv, scope)
        ret = ftype.split(":", 1)[1] or "any"
        e.inferred_type = "any" if ret in ("", "any") else ret
        e.field_callable = True  # type: ignore
        return True

    def _field_is_bool(self, class_name: str, field_name: str) -> bool:
        """True if `field_name` was annotated `bool` on `class_name` or any
        ancestor. Walks the parent chain exactly as `_resolve_field_type` does."""
        seen: set = set()
        cur = class_name
        while cur is not None and cur in self.classes and cur not in seen:
            seen.add(cur)
            _cs = self.classes[cur]
            if field_name in getattr(_cs, "field_bools", set()):
                return True
            cur = _cs.parent
        return False

    def _resolve_field_type(self, class_name: str, field_name: str) -> Optional[str]:
        """Walk the parent chain to find the static type of an instance field.
        Returns the type string or None if no class in the chain declares it."""
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls: ClassSig = self.classes.get(cur)
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
            cls: ClassSig = self.classes.get(cur)
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
            cls: ClassSig = self.classes.get(cur)
            if cls is None:
                return []
            if field_name in cls.field_tuple_types:
                return list(cls.field_tuple_types[field_name])
            if field_name in cls.fields:
                return []
            cur = cls.parent
        return []

    def _resolve_field_inner_value(self, class_name: str, field_name: str) -> str:
        """One-more-level collection metadata for a list/dict field.

        For `list[dict[K,V]]` / `list[list[T]]` fields this is the nested dict
        value / list element kind recovered after indexing or iteration once.
        The same applies to dict-valued fields whose values are themselves
        dicts/lists. Returns "int" when unknown."""
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls: ClassSig = self.classes.get(cur)
            if cls is None:
                return "int"
            if field_name in cls.field_inner_value_types:
                return cls.field_inner_value_types[field_name]
            if field_name in cls.fields:
                return "int"
            cur = cls.parent
        return "int"

    def _resolve_field_value_tuple(self, class_name: str, field_name: str) -> list:
        """Per-slot kinds for tuple elements/values inside list/dict fields.

        Used for `list[tuple[...]]` and `dict[str, tuple[...]]` fields so
        `self.rows[i][0]` / `for a, b in self.rows` keeps the tuple slot shape."""
        cur = class_name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            cls: ClassSig = self.classes.get(cur)
            if cls is None:
                return []
            if field_name in cls.field_value_tuple_types:
                return list(cls.field_value_tuple_types[field_name])
            if field_name in cls.fields:
                return []
            cur = cls.parent
        return []

    # ---- instance field type inference --------------------------------------

    def _literal_shape_el_type(self, e: A.ListLit) -> str | None:
        """Cheap, `_check_expr`-free element-kind guess for a list literal's
        own elements (str/float/int literals only -- enough for the
        unannotated-class-var case, which is what needs this before any body
        has been checked). None if empty or not a simple homogeneous literal
        scalar kind."""
        kinds: set[str] = set()
        for el in e.elems:
            if isinstance(el, A.StrLit):
                kinds.add("str")
            elif isinstance(el, A.FloatLit):
                kinds.add("float")
            elif isinstance(el, A.IntLit):
                kinds.add("int")
            else:
                return None
        if len(kinds) == 1:
            # Explicit loop, not next(iter(kinds)): codegen has no support
            # for iter()/next() (this is the compiler's own source, self-
            # compiled) -- kinds has exactly one element here, so the loop
            # body runs once and returns it.
            for kind in kinds:
                return kind
        return None

    def _literal_shape_value_type(self, e: A.DictLit) -> str | None:
        """Same idea as `_literal_shape_el_type`, but for a dict literal's
        values."""
        kinds: set[str] = set()
        for v in e.values:
            if isinstance(v, A.StrLit):
                kinds.add("str")
            elif isinstance(v, A.FloatLit):
                kinds.add("float")
            elif isinstance(v, A.IntLit):
                kinds.add("int")
            else:
                return None
        if len(kinds) == 1:
            # Explicit loop, not next(iter(kinds)): codegen has no support
            # for iter()/next() (this is the compiler's own source, self-
            # compiled) -- kinds has exactly one element here, so the loop
            # body runs once and returns it.
            for kind in kinds:
                return kind
        return None

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
                    # Subscript reads with an explicit `ty: str` annotation,
                    # not `ty, el, val, _tup, _elval = r`: r's own slot-0
                    # static type doesn't reliably land as "str"/"any" through
                    # a tuple-unpack (a selfhost-only divergence -- see
                    # _resolve_annot's matching fix), so every downstream
                    # `ty == "list"` / `ty == "dict"` check could compile as
                    # a raw pointer comparison instead of _runtime_str_eq.
                    ty: str = r[0]
                    el = r[1]
                    val = r[2]
                    tup = r[3]
                    elval = r[4]
                elif cvalue is not None:
                    lit = self._literal_arg_type(cvalue)
                    if lit is not None:
                        ty = lit[0]
                        el = lit[1]
                        val = lit[2]
                        tup = lit[3]
                        elval = None
                    else:
                        ty, el, val, _tup = self._static_value_info(cvalue, {})
                        tup = _tup
                        elval = None
                    # `_check_expr` hasn't run on `cvalue` yet at this point in
                    # `analyze()` (this pass runs before any body is checked),
                    # so a DictLit/ListLit's own `value_type`/`el_type`
                    # attributes aren't populated yet -- read the literal's
                    # element shape directly instead. Without this, an
                    # unannotated `NAME = {"a": "b", ...}` class var always
                    # fell back to "int" for its value kind (None here, then
                    # "int" downstream wherever a value-kind default applies),
                    # even when every value is a string. A `self.NAME[k]`
                    # subscript read then carried the wrong static type,
                    # which only became visible when the result was used
                    # somewhere that branches on the static type (e.g. an
                    # f-string segment's int-vs-str formatting) -- confirmed
                    # via gdb on a selfhost rebuild: codegen.py's own
                    # `self.SETCC[op]` (SETCC: dict[str, str], unannotated)
                    # read back "int", so `f"{setcc} al"` applied int-to-str
                    # conversion to what's actually a string pointer,
                    # corrupting the generated NASM text.
                    if el is None and isinstance(cvalue, A.ListLit):
                        el = self._literal_shape_el_type(cvalue)
                    if val is None and isinstance(cvalue, A.DictLit):
                        val = self._literal_shape_value_type(cvalue)
                else:
                    ty, el, val = "int", None, None
                sig.fields[cname] = ty
                if isinstance(cannot, tuple) and cannot and cannot[0] == "bool":
                    sig.field_bools.add(cname)
                if ty == "list" and el is not None:
                    sig.field_el_types[cname] = el
                    if el in ("list", "dict") and elval is not None:
                        sig.field_inner_value_types[cname] = elval
                    elif el == "tuple" and tup:
                        sig.field_value_tuple_types[cname] = tup
                elif ty == "dict" and val is not None:
                    sig.field_el_types[cname] = val
                    if val in ("list", "dict") and elval is not None:
                        sig.field_inner_value_types[cname] = elval
                    elif val == "tuple" and tup:
                        sig.field_value_tuple_types[cname] = tup
            for m in c.methods:
                # Each param maps to its resolved annotation tuple
                # (ty, el, val, tuple) so a `self.x = param` assignment can carry
                # the param's element/value kinds onto the field.
                pinfo: dict = {}
                m_param_types_x: list = m.param_types
                m_defaults_x: list = m.defaults
                # Explicit `: list` intermediates: m is opaque to sema (external
                # FuncDef), so m.params / m.body read back as opaque "any".
                # Without the cast, enumerate(m.params) / _scan_field_assigns(m.body)
                # use the wrong codegen path (int/dict ops instead of list ops).
                m_params_cf: list = m.params
                m_body_cf: list = m.body
                for i, p in enumerate(m_params_cf):
                    if i == 0:
                        continue  # self
                    annot = m_param_types_x[i] if i < len(m_param_types_x) else None
                    r = self._resolve_annot(annot)  # type: ignore
                    if r is not None:
                        pinfo[p] = r
                    elif i < len(m_defaults_x) and m_defaults_x[i] is not None:
                        # A `=None` default carries no real type of its own
                        # (its literal is IntLit(0, is_none=True)) -- same
                        # fix as codegen's param-type setup: "any" instead
                        # of trusting expr_type's "int", so a later
                        # `self.x = param` doesn't mistype `self.x` as int
                        # for an Optional[X]-style parameter.
                        dty = "any" if A.is_none_expr(m_defaults_x[i]) else A.expr_type(m_defaults_x[i])  # type: ignore
                        pinfo[p] = (dty, None, None, None, None)
                    else:
                        inferred = self.inferred_param_types.get(f"{c.name}.{m.name}:{i}")
                        if inferred is not None:
                            pinfo[p] = inferred
                        else:
                            # A field assigned from an unannotated parameter
                            # with no literal call-site signal is genuinely
                            # dynamic. Keep it opaque rather than pinning the
                            # field to the numeric "int" fallback, so later
                            # `self.field.method()` / `self.field[...]` uses
                            # remain lenient.
                            pinfo[p] = ("any", None, None, None, None)
                self._scan_field_assigns(m_body_cf, sig, pinfo)

        # Every class sig is populated now, so the class-qualified spelling of
        # the same rule can run over the whole module: `ClassName.x = <value>`
        # from module level, a function body, or any method teaches the same
        # tables `self.x = <value>` just did (see _scan_class_var_assigns).
        self._scan_class_var_assigns(self.mod.body)
        for f in self.mod.funcs:
            self._scan_class_var_assigns(f.body)
        for c in self.mod.classes:
            for m in c.methods:
                self._scan_class_var_assigns(m.body)

    def _value_shape(self, value, pinfo: dict):
        """(ty, el, val, tup) of an assigned value, with a container literal's
        own element/value kind recovered from its contents.

        `_static_value_info`/`_literal_arg_type` deliberately report a bare
        ("list"/"dict", None, None, None) for a container literal, because
        `_check_expr` hasn't stamped `el_type`/`value_type` onto the node yet
        at field-collection time. The literal's elements are right there in
        the syntax, so read the shape off them directly -- otherwise an
        unannotated `x = {"a": "b"}` never learns its "str" value kind and
        every later `x[k]` read falls back to the "int" unknown sentinel,
        which codegen formats as a decimal integer instead of dereferencing
        as a string."""
        raw = self._static_value_info(value, pinfo)
        ty, el, val, tup = raw[0], raw[1], raw[2], raw[3]
        if el is None and isinstance(value, A.ListLit):
            el = self._literal_shape_el_type(value)
        if val is None and isinstance(value, A.DictLit):
            val = self._literal_shape_value_type(value)
        return ty, el, val, tup

    def _teach_container_shape(
        self, sig: ClassSig, name: str, ty: str, el, val, tup, elval
    ) -> None:
        """Record a container field/class-var's element/value kinds, filling in
        only what isn't already known.

        Deliberately independent of the field's own type-update guard in
        `_scan_field_assigns`: a field is routinely typed "dict"/"list" by an
        EMPTY initializer (`self.x = {}`, or a `x = {}` class-body default)
        that carries no element kind at all, so the only statement that knows
        the real kind is a later assignment -- which that guard skips, since
        the type itself isn't changing. The `.append()` / `self.x[k] = v`
        mutation rules below exist for exactly this gap; this is the same
        rule for plain assignment."""
        if ty == "list" and el is not None:
            if name not in sig.field_el_types:
                sig.field_el_types[name] = el
            if el in ("list", "dict"):
                if elval is not None and name not in sig.field_inner_value_types:
                    sig.field_inner_value_types[name] = elval
            elif el == "tuple" and tup and name not in sig.field_value_tuple_types:
                sig.field_value_tuple_types[name] = tup
        elif ty == "dict" and val is not None:
            if name not in sig.field_el_types:
                sig.field_el_types[name] = val
            if val in ("list", "dict"):
                if elval is not None and name not in sig.field_inner_value_types:
                    sig.field_inner_value_types[name] = elval
            elif val == "tuple" and tup and name not in sig.field_value_tuple_types:
                sig.field_value_tuple_types[name] = tup
        elif ty == "tuple" and tup and name not in sig.field_tuple_types:
            sig.field_tuple_types[name] = tup

    def _scan_class_var_assigns(self, stmts: list) -> None:
        """Teach class-level container vars their element/value kind from
        `ClassName.x = <value>` assignments, wherever they appear.

        `self.x = <value>` inside a method is already scanned by
        `_scan_field_assigns`, but a class var is just as often (re)assigned
        through the class itself, from outside any method -- a module-level
        registry/configuration table is the common shape. Both spellings
        address the identical `__cv_<Class>__<name>` storage and are read
        back through the same field tables, so both must teach those tables;
        otherwise a class body's empty `x = {}` default leaves the value kind
        unknown forever no matter what is later assigned to it."""
        for s in stmts:
            if (
                isinstance(s, A.AttrAssign)
                and isinstance(s.obj, A.Name)
                and s.obj.name in self.classes
            ):
                sig = self.classes[s.obj.name]
                # Only names the class actually declares -- never invent a
                # field from an assignment to something else.
                if s.name in sig.fields:
                    ty, el, val, tup = self._value_shape(s.value, {})
                    self._teach_container_shape(sig, s.name, ty, el, val, tup, None)
            elif isinstance(s, A.If):
                self._scan_class_var_assigns(s.then)
                self._scan_class_var_assigns(s.orelse)
            elif isinstance(s, A.While):
                self._scan_class_var_assigns(s.body)
            elif isinstance(s, A.For):
                self._scan_class_var_assigns(s.body)
            elif isinstance(s, A.Try):
                self._scan_class_var_assigns(s.body)
                self._scan_class_var_assigns(s.handler)
                for _types, _bind, hbody in s.extra_handlers:
                    self._scan_class_var_assigns(hbody)
                self._scan_class_var_assigns(s.else_body)
                self._scan_class_var_assigns(s.finally_body)

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
                _raw_annot = getattr(s, "annot", None)
                if (
                    isinstance(_raw_annot, tuple)
                    and _raw_annot
                    and _raw_annot[0] == "bool"
                ):
                    # `self.x: bool` -- the same rendering flag the class-var
                    # path records, for the instance-attribute spelling.
                    sig.field_bools.add(s.name)
                r = self._resolve_annot(_raw_annot)  # type: ignore
                if r is not None:
                    # Same fix as elsewhere: subscript reads with an
                    # explicit `ty: str`, not a tuple-unpack.
                    ty: str = r[0]
                    el = r[1]
                    val = r[2]
                    tup = r[3]
                    elval = r[4]
                    # A BARE `list`/`dict` annotation resolves its element kind
                    # to "any", but "any" here means UNWRITTEN, not "the author
                    # declared a heterogeneous container". Recording it pins the
                    # field as heterogeneous, which BOXES every element written
                    # in -- so `self.items = []` (whose annotation is the bare
                    # `list` synthesized from the initializer) followed by
                    # `self.items.append(1)` printed pointers instead of ints.
                    # The raw annotation still distinguishes the two: an
                    # explicit `list[object]` carries an element part, a bare
                    # `list` does not.
                    if (
                        isinstance(_raw_annot, tuple)
                        and len(_raw_annot) >= 2
                        and _raw_annot[1] is None
                    ):
                        if el == "any":
                            el = None
                        if val == "any":
                            val = None
                else:
                    ty, el, val, tup = self._value_shape(s.value, pinfo)
                    elval = None
                    # An EMPTY container literal has no element kind, and
                    # `_value_shape` reports that absence as "any". Teaching
                    # "any" pins the field as a genuinely HETEROGENEOUS
                    # container, which makes every element written into it get
                    # BOXED -- so `self.items = []` followed by
                    # `self.items.append(1)` printed pointers instead of ints.
                    # An explicit `self.items: list[object] = []` still records
                    # "any", because that goes through the annotation branch
                    # above, where the author really did say so.
                    if (
                        isinstance(s.value, A.ListLit) and not s.value.elems
                    ) or (
                        isinstance(s.value, A.DictLit) and not s.value.values
                    ):
                        el = None
                        val = None
                existing = sig.fields.get(s.name)
                # Don't let a later `= 0` reset placeholder downgrade a field we
                # already typed more precisely.
                if existing is None or (existing == "int" and ty != "int"):
                    sig.fields[s.name] = ty
                # The element/value kind is learnable from any assignment that
                # reveals it, including one the guard above skips because the
                # field's own type isn't changing (`self.x = {}` then a later
                # `self.x = {...}`). See _teach_container_shape.
                self._teach_container_shape(sig, s.name, ty, el, val, tup, elval)
            elif (
                isinstance(s, A.ExprStmt)
                and isinstance(s.expr, A.MethodCall)
                and s.expr.method == "append"
                and len(s.expr.args) == 1
                and isinstance(s.expr.obj, A.Attr)
                and isinstance(s.expr.obj.obj, A.Name)
                and s.expr.obj.obj.name == "self"
            ):
                # `self.xs.append(v)` -- a list field initialized empty
                # (`self.xs = []`) has no element-kind info from its
                # initializer alone (_literal_shape_el_type returns None for
                # an empty ListLit). Without this, `field_el_types` is never
                # set, so every later `self.xs[i]` read/compare defaults to
                # "int" -- e.g. a `self.names[i] == name` string comparison
                # then compiles as raw pointer equality instead of
                # _runtime_str_eq, since the comparison codegen only takes
                # the string-compare path when a static "str" type is known.
                fname = s.expr.obj.name
                if fname not in sig.field_el_types:
                    el = self._static_value_info(s.expr.args[0], pinfo)[0]
                    # "any" is not a kind, it is the ABSENCE of one -- this scan
                    # runs before parameter inference, so `self.items.append(x)`
                    # on an unannotated parameter sees nothing. Recording "any"
                    # anyway pinned the field as a heterogeneous list, which
                    # BOXES every element written into it, so `self.items = [];
                    # self.items.append(1); print(self.items)` printed pointers.
                    # Teaching nothing leaves the field at the unknown-int
                    # sentinel, which stores raw -- the right default.
                    if el not in ("int", "any"):
                        sig.field_el_types[fname] = el
            elif (
                isinstance(s, A.IndexAssign)
                and isinstance(s.target.obj, A.Attr)
                and isinstance(s.target.obj.obj, A.Name)
                and s.target.obj.obj.name == "self"
            ):
                # `self.xs[k] = v` -- same gap as `.append()` above, but for
                # a dict field initialized empty (`self.xs = {}`).
                fname = s.target.obj.name
                if sig.fields.get(fname) == "dict" and fname not in sig.field_el_types:
                    val = self._static_value_info(s.value, pinfo)[0]
                    if val != "int":
                        sig.field_el_types[fname] = val
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
        if isinstance(value, A.StrLit) or isinstance(value, A.FString):
            return ("str", None, None, None)
        if isinstance(value, A.ListLit):
            return ("list", None, None, None)
        if isinstance(value, A.DictLit):
            return ("dict", None, None, None)
        if isinstance(value, A.SetLit):
            return ("set", None, None, None)
        if isinstance(value, A.TupleLit):
            return ("tuple", None, None, None)
        if isinstance(value, A.Call) and value.func in (
            "str", "repr", "chr", "hex", "oct", "bin", "format", "input",
        ):
            # Builtins with a fixed result kind. Knowable from syntax alone,
            # which is this function's contract -- and the append/return scans
            # that call it bail entirely on one unknown value, so recognizing
            # these decides whether a whole list gets an element kind.
            return ("str", None, None, None)
        if isinstance(value, A.Call) and value.func in (
            "len", "ord", "int", "round", "abs", "id", "hash",
        ):
            return ("int", None, None, None)
        if isinstance(value, A.Call) and value.func == "float":
            return ("float", None, None, None)
        if isinstance(value, A.BoolOp):
            # `a or b` / `a and b` evaluate to ONE of the operands, so when both
            # sides are the same knowable kind the result is that kind. A single
            # unknown side leaves it unknown rather than guessing.
            _bl = self._literal_arg_type(value.left)
            _br = self._literal_arg_type(value.right)
            if _bl is not None and _br is not None and _bl[0] == _br[0]:
                return (_bl[0], None, None, None)
        if isinstance(value, A.BinOp) and value.op == "/":
            # TRUE DIVISION always yields a float in Python 3, whatever the
            # operands are. Without this, `def half(x): return x / 2` inferred
            # an int return and the caller printed the double's raw bits as a
            # pointer-sized integer.
            return ("float", None, None, None)
        if isinstance(value, A.BinOp) and value.op in ("+", "-", "*", "%", "**"):
            # Numeric promotion: a float on either side makes the result float.
            # Only literal-shaped operands are consulted (this runs before any
            # expression has been checked), the same standard the rest of this
            # function holds itself to.
            _lhs = self._literal_arg_type(value.left)
            _rhs = self._literal_arg_type(value.right)
            if (_lhs is not None and _lhs[0] == "float") or (
                _rhs is not None and _rhs[0] == "float"
            ):
                return ("float", None, None, None)
            if (
                value.op == "+"
                and _lhs is not None and _rhs is not None
                and _lhs[0] == "str" and _rhs[0] == "str"
            ):
                return ("str", None, None, None)
        if isinstance(value, A.Lambda):
            # A lambda passed as an argument / stored in a field is a CALLABLE
            # VALUE (a code pointer). Typing the receiving parameter/field
            # `callable:<ret>` rather than the `int` last resort is what makes
            # `self.fn = fn; self.fn(x)` and `apply(fn, v)` work off one
            # mechanism -- see `_callable_type_of`. Its return kind isn't
            # checked yet at inference time (the body hasn't been walked), so
            # take the annotation-free `any`; the call's result stays opaque,
            # which is correct for a callable whose signature is unknown.
            return ("callable:" + (getattr(value, "lambda_ret", None) or "any"),
                    None, None, None)
        if isinstance(value, A.Name) and value.name in self.funcs and value.name not in self.classes:
            # A bare function reference (`register(handler)`), same deal --
            # here the callee's real return kind IS known from its signature.
            _fsig = self.funcs[value.name]
            _frt = getattr(_fsig, "ret_type", None)
            return ("callable:" + (_frt[0] if _frt else "int"), None, None, None)
        if isinstance(value, A.Call) and value.func in ("bytes", "bytearray"):
            return ("list", "int", None, None)
        if isinstance(value, A.Call) and value.func in self.classes:
            return (f"instance:{value.func}", None, None, None)
        if isinstance(value, A.Name) and (
            value.name in self.classes
            or value.name in BUILTIN_EXCEPTIONS
            or value.name in BUILTIN_TYPE_NAMES
        ):
            # A bare class name passed as an argument is a first-class `type`
            # value (its RTTI id) -- lets an unannotated parameter that only
            # ever receives a class (a registry's `register(self, name, cls)`,
            # `cls.attr = ...`) be inferred `type` rather than the `int`
            # last-resort, so attribute stores/reads through it dispatch on the
            # class object instead of faulting on the raw id.
            return ("type", None, None, None)
        if isinstance(value, A.Comprehension):
            # `[Trite() for _ in range(16)]` -- elt's syntactic type becomes
            # the produced list's element kind, same as a literal list would
            # get from its own elements. `_check_expr` hasn't stamped
            # `elt`/the comprehension itself yet at this point (field
            # collection runs before body analysis), so the element kind
            # can't come from `list_el_type` -- recurse into the same
            # syntax-only resolution used for literals instead.
            elt_info = self._literal_arg_type(value.elt)
            el = elt_info[0] if elt_info is not None else None
            return ("list", el, None, None)
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
        if isinstance(value, A.Call) or isinstance(value, A.MethodCall) or isinstance(value, A.Attr):
            # Unknown calls/attributes in field initializers are commonly
            # external objects (pygame surfaces, file handles, etc.). Treat
            # them as opaque instead of the numeric fallback so later member
            # access on the field remains lenient.
            return ("any", None, None, None)
        return ("int", None, None, None)

    # ---- call-site argument type inference for unannotated parameters ------

    def _collect_calls_stmts(self, stmts: list, out: list) -> None:
        """Recursively collect every `A.Call`/`A.MethodCall` reachable from a
        statement list, via an explicit walk over every statement/expression
        shape. Used by `_infer_unannotated_params` to find every call site of
        every function/method in the module, regardless of how deeply it's
        nested in expressions."""
        for s in stmts:
            if isinstance(s, A.Assign):
                self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.AugAssign):
                self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.TupleAssign):
                for t in s.targets:
                    if isinstance(t, A.Subscript) or isinstance(t, A.Attr):
                        self._collect_calls_expr(t, out)
                for v in s.values:
                    self._collect_calls_expr(v, out)
            elif isinstance(s, A.MultiAssign):
                self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.Return):
                if s.value is not None:
                    self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.If):
                self._collect_calls_expr(s.test, out)
                self._collect_calls_stmts(s.then, out)
                self._collect_calls_stmts(s.orelse, out)
            elif isinstance(s, A.While):
                self._collect_calls_expr(s.test, out)
                self._collect_calls_stmts(s.body, out)
                self._collect_calls_stmts(s.orelse, out)
            elif isinstance(s, A.For):
                for a in s.range_args:
                    self._collect_calls_expr(a, out)
                if s.iter is not None:
                    self._collect_calls_expr(s.iter, out)
                self._collect_calls_stmts(s.body, out)
                self._collect_calls_stmts(s.orelse, out)
            elif isinstance(s, A.ExprStmt):
                self._collect_calls_expr(s.expr, out)
            elif isinstance(s, A.AttrAssign):
                self._collect_calls_expr(s.obj, out)
                self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.IndexAssign):
                self._collect_calls_expr(s.target, out)
                self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.With):
                self._collect_calls_expr(s.expr, out)
                self._collect_calls_stmts(s.body, out)
            elif isinstance(s, A.Try):
                self._collect_calls_stmts(s.body, out)
                self._collect_calls_stmts(s.handler, out)
                for _types, _bind, hbody in s.extra_handlers:
                    self._collect_calls_stmts(hbody, out)
                self._collect_calls_stmts(s.else_body, out)
                self._collect_calls_stmts(s.finally_body, out)
            elif isinstance(s, A.Raise):
                if s.value is not None:
                    self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.Del):
                self._collect_calls_expr(s.target, out)
            elif isinstance(s, A.YieldStmt):
                self._collect_calls_expr(s.value, out)
            elif isinstance(s, A.Match):
                self._collect_calls_expr(s.subject, out)
                for _pattern, guard, body in s.cases:
                    if guard is not None:
                        self._collect_calls_expr(guard, out)
                    self._collect_calls_stmts(body, out)
            # Break/Continue/Pass/Import/FromImport/Global/Nonlocal/ClosureBind
            # carry no nested expressions worth scanning (ClosureBind's lifted
            # function body is a separate top-level FuncDef already walked on
            # its own by _infer_unannotated_params's callers).

    def _collect_calls_expr(self, e, out: list) -> None:
        if isinstance(e, A.Call):
            out.append(e)
            for a in e.args:
                self._collect_calls_expr(a, out)
            for _kn, kv in e.kwargs:
                self._collect_calls_expr(kv, out)
        elif isinstance(e, A.MethodCall):
            out.append(e)
            self._collect_calls_expr(e.obj, out)
            for a in e.args:
                self._collect_calls_expr(a, out)
            for _kn, kv in e.kwargs:
                self._collect_calls_expr(kv, out)
        elif isinstance(e, A.BinOp):
            self._collect_calls_expr(e.left, out)
            self._collect_calls_expr(e.right, out)
        elif isinstance(e, A.UnaryOp):
            self._collect_calls_expr(e.operand, out)
        elif isinstance(e, A.Compare):
            for o in e.operands:
                self._collect_calls_expr(o, out)
        elif isinstance(e, A.BoolOp):
            self._collect_calls_expr(e.left, out)
            self._collect_calls_expr(e.right, out)
        elif isinstance(e, A.IfExp):
            self._collect_calls_expr(e.test, out)
            self._collect_calls_expr(e.body, out)
            self._collect_calls_expr(e.orelse, out)
        elif isinstance(e, A.NamedExpr):
            self._collect_calls_expr(e.value, out)
        elif isinstance(e, A.ListLit):
            for el in e.elems:
                self._collect_calls_expr(el, out)
        elif isinstance(e, A.Subscript):
            self._collect_calls_expr(e.obj, out)
            if isinstance(e.index, A.Slice):
                if e.index.start is not None:
                    self._collect_calls_expr(e.index.start, out)
                if e.index.stop is not None:
                    self._collect_calls_expr(e.index.stop, out)
                if e.index.step is not None:
                    self._collect_calls_expr(e.index.step, out)
            else:
                self._collect_calls_expr(e.index, out)
        elif isinstance(e, A.Attr):
            self._collect_calls_expr(e.obj, out)
        elif isinstance(e, A.FString):
            for seg in e.segments:
                self._collect_calls_expr(seg, out)
        elif isinstance(e, A.DictLit):
            for k in e.keys:
                if k is not None:
                    self._collect_calls_expr(k, out)
            for v in e.values:
                self._collect_calls_expr(v, out)
        elif isinstance(e, A.TupleLit):
            for el in e.elems:
                self._collect_calls_expr(el, out)
        elif isinstance(e, A.SetLit):
            for el in e.elems:
                self._collect_calls_expr(el, out)
        elif isinstance(e, A.Starred):
            self._collect_calls_expr(e.value, out)
        elif isinstance(e, A.Comprehension):
            self._collect_calls_expr(e.elt, out)
            self._collect_calls_expr(e.iter, out)
            if e.cond is not None:
                self._collect_calls_expr(e.cond, out)
            for ei in e.extra_for_iters:
                self._collect_calls_expr(ei, out)
            for ec in e.extra_for_conds:
                if ec is not None:
                    self._collect_calls_expr(ec, out)
        elif isinstance(e, A.DictComprehension):
            self._collect_calls_expr(e.key, out)
            self._collect_calls_expr(e.value, out)
            self._collect_calls_expr(e.iter, out)
            if e.cond is not None:
                self._collect_calls_expr(e.cond, out)
        elif isinstance(e, A.Lambda):
            if e.body is not None:
                self._collect_calls_expr(e.body, out)

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
        self._collect_calls_stmts(self.mod.body, calls)
        for f in self.mod.funcs:
            self._collect_calls_stmts(f.body, calls)
        for c in self.mod.classes:
            for m in c.methods:
                self._collect_calls_stmts(m.body, calls)

        _mod_names = {
            _mn for _mn in getattr(self.mod, "project_module_qualifiers", set())
        }
        for f in self.mod.funcs:
            sites = [c for c in calls if isinstance(c, A.Call) and c.func == f.name]
            # A MODULE-QUALIFIED call (`pprint.pformat(x)`) is a call to this
            # same merged function -- whole-program compilation flattens the
            # namespace -- but it is still an A.MethodCall at THIS point,
            # because the rewrite to a plain Call happens during checking,
            # which runs later. Collecting only A.Call left every such callee's
            # parameters uninferred: `pformat`'s `o` defaulted to int, so its
            # `str(o)` formatted a dict POINTER as a decimal integer.
            for _c in calls:
                if (
                    isinstance(_c, A.MethodCall)
                    and _c.method == f.name
                    and isinstance(_c.obj, A.Name)
                    and _c.obj.name not in self.imported_modules
                ):
                    sites.append(_c)
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

    def _infer_call_target_params(
        self, qualname: str, fn: A.FuncDef, sites: list, start: int
    ) -> None:
        """Infer types for `fn`'s parameters at index >= `start` (0 for plain
        functions, 1 for methods to skip `self`) from `sites` (the `A.Call`/
        `A.MethodCall` nodes invoking it), storing results in
        `self.inferred_param_types`. See `_infer_unannotated_params`."""
        fn_param_types: list = fn.param_types
        fn_defaults: list = fn.defaults
        for i, p in enumerate(fn.params):
            if i < start:
                continue
            annot = fn_param_types[i] if i < len(fn_param_types) else None
            if self._resolve_annot(annot) is not None:  # type: ignore
                continue
            if i < len(fn_defaults) and fn_defaults[i] is not None:
                continue
            candidates: list = []
            found_any = False
            arg_idx = i - start
            for site in sites:
                args: list = site.args
                if arg_idx < len(args):
                    arg = args[arg_idx]
                else:
                    # Explicit loop, not next((v for n,v in ... if n==p), None):
                    # asmpython's own codegen has no generator-expression/next()
                    # support (this is the compiler's own source, self-compiled),
                    # so that construct silently fell through to an unrelated
                    # fallback and corrupted state instead of erroring.
                    site_kwargs: list = site.kwargs
                    arg = None
                    for kw_name, kw_val in site_kwargs:
                        if kw_name == p:
                            arg = kw_val
                            break
                    if arg is None:
                        continue
                lit = self._literal_arg_type(arg)
                if lit is None:
                    continue
                found_any = True
                if lit not in candidates:
                    candidates.append(lit)
            if found_any and len(candidates) == 1:
                self.inferred_param_types[f"{qualname}:{i}"] = candidates[0]

    def _apply_inferred_float_params(self) -> None:
        """Propagate a FLOAT parameter type inferred from call sites (see
        `_infer_unannotated_params`) into the parameter's ABI, not just the
        body type-check scope.

        Float is the one inferred kind whose calling convention differs from
        the `any`/int default -- a float argument travels in an XMM register,
        an int/pointer in a GP register. `_infer_unannotated_params` records
        the inferred kind and `_seed_param` types the body from it, but the
        FuncSig's `param_types` (how a caller marshals the argument) and the
        FuncDef's `param_types` (the callee's own IR parameter ABI, read
        directly by ir_lower) both stayed `any`. So `def add(a, b): return
        a + b` called `add(1.5, 2.5)` emitted float arithmetic in the body
        while the caller passed -- and the callee read -- the operands through
        GP registers, reinterpreting each float's 64 bits as an integer and
        returning garbage (2.65e-314). Writing the float type into BOTH tables
        keeps every consumer in agreement. Pointer-shaped inferred kinds
        (str/list/dict/set/instance) share the GP convention with `any`, so
        they need no ABI change and keep their existing scope-only seeding.
        Only fills a genuinely unannotated slot -- an explicit annotation is
        always authoritative."""
        def _apply(qualname: str, fn, sig, start: int) -> None:
            fn_pts: list = fn.param_types
            n: int = len(fn.params)
            for i in range(n):
                if i < start:
                    continue
                inferred = self.inferred_param_types.get(f"{qualname}:{i}")
                if inferred is None or inferred[0] != "float":
                    continue
                cur = fn_pts[i] if i < len(fn_pts) else None
                if cur is not None:
                    continue  # never override a real annotation
                while len(fn_pts) <= i:
                    fn_pts.append(None)
                fn_pts[i] = ("float", None)
                if sig is not None:
                    sig_pts: list = sig.param_types
                    if i < len(sig_pts):
                        sig_pts[i] = "float"
        for f in self.mod.funcs:
            _apply(f.name, f, self.funcs.get(f.name), 0)
        for c in self.mod.classes:
            csig = self.classes.get(c.name)
            for m in c.methods:
                msig = csig.methods.get(m.name) if csig is not None else None
                _apply(f"{c.name}.{m.name}", m, msig, 1)

    def _infer_lambda_param_types(self, target: str, value: "A.Lambda") -> None:
        """Same idea as `_infer_call_target_params`, but for a lambda bound
        directly to a name (`greet = lambda name: "hi " + name`).

        `_infer_unannotated_params()` runs as a pre-pass over `self.mod.funcs`
        before any lambda has been discovered/synthesised (lambdas are only
        turned into a real `A.FuncDef` the first time `_check_expr` walks
        past the `A.Lambda` node, during the main body-checking pass), and it
        matches call sites by the callee's literal name -- but a lambda is
        always invoked through the variable it's bound to (`greet(...)`), not
        through its synthesised `_lambda_<hex>` name, so it would never be
        found anyway. Without this, every lambda parameter silently defaults
        to `int` (`_seed_param`'s last resort), so `"hi " + name` raised a
        spurious `str + int` sema error even though every real call site
        passes a string.
        """
        lname = getattr(value, "func_name", None)
        if lname is None:
            return
        calls: list = []
        self._collect_calls_stmts(self.mod.body, calls)
        for f in self.mod.funcs:
            self._collect_calls_stmts(f.body, calls)
        for c in self.mod.classes:
            for m in c.methods:
                self._collect_calls_stmts(m.body, calls)
        sites = [c for c in calls if isinstance(c, A.Call) and c.func == target]
        if not sites:
            return
        for i in range(len(value.params)):
            key = f"{lname}:{i}"
            if key in self.inferred_param_types:
                continue
            candidates: list = []
            found_any = False
            for site in sites:
                args: list = site.args
                if i >= len(args):
                    continue
                lit = self._literal_arg_type(args[i])
                if lit is None:
                    continue
                found_any = True
                if lit not in candidates:
                    candidates.append(lit)
            if found_any and len(candidates) == 1:
                self.inferred_param_types[key] = candidates[0]

    def _param_usage_hints(self, params: list, body: list) -> dict:
        """Last-resort hints for unannotated parameters from their own body.

        Call-site inference handles the common literal cases. Some Pythonic
        methods are only called indirectly, though (notably `__setitem__`), so
        a parameter used as `param[...]` should not default to numeric `int`.
        Mark it opaque instead: indexing/attribute checks stay lenient without
        weakening explicitly annotated ints.
        """
        hints: dict = {}

        def note_indexable(e) -> None:
            if isinstance(e, A.Name) and e.name in params:
                hints[e.name] = "any"

        def scan_expr(e) -> None:
            if e is None:
                return
            if isinstance(e, A.Subscript):
                note_indexable(e.obj)
                scan_expr(e.obj)
                scan_expr(e.index)
            elif isinstance(e, A.BinOp):
                scan_expr(e.left)
                scan_expr(e.right)
            elif isinstance(e, A.UnaryOp):
                scan_expr(e.operand)
            elif isinstance(e, A.Compare):
                for opnd in e.operands:
                    scan_expr(opnd)
            elif isinstance(e, A.BoolOp):
                scan_expr(e.left)
                scan_expr(e.right)
            elif isinstance(e, A.IfExp):
                scan_expr(e.test)
                scan_expr(e.body)
                scan_expr(e.orelse)
            elif isinstance(e, A.NamedExpr):
                scan_expr(e.value)
            elif isinstance(e, A.Call):
                for a in e.args:
                    scan_expr(a)
                for _kn, kv in e.kwargs:
                    scan_expr(kv)
                if e.dstar is not None:
                    scan_expr(e.dstar)
            elif isinstance(e, A.MethodCall):
                scan_expr(e.obj)
                for a in e.args:
                    scan_expr(a)
                for _kn, kv in e.kwargs:
                    scan_expr(kv)
            elif isinstance(e, A.Attr):
                scan_expr(e.obj)
            elif isinstance(e, A.ListLit):
                for x in e.elems:
                    scan_expr(x)
            elif isinstance(e, A.TupleLit):
                for x in e.elems:
                    scan_expr(x)
            elif isinstance(e, A.SetLit):
                for x in e.elems:
                    scan_expr(x)
            elif isinstance(e, A.DictLit):
                for k in e.keys:
                    scan_expr(k)
                for v in e.values:
                    scan_expr(v)
            elif isinstance(e, A.Comprehension):
                scan_expr(e.iter)
                scan_expr(e.elt)
                scan_expr(e.cond)
            elif isinstance(e, A.DictComprehension):
                scan_expr(e.iter)
                scan_expr(e.key)
                scan_expr(e.value)
                scan_expr(e.cond)
            elif isinstance(e, A.Lambda):
                scan_expr(e.body)
            elif isinstance(e, A.Starred):
                scan_expr(e.value)
            elif isinstance(e, A.DoubleStarred):
                scan_expr(e.value)

        def scan_stmts(stmts: list) -> None:
            for s in stmts:
                if isinstance(s, A.Assign):
                    scan_expr(s.value)
                elif isinstance(s, A.Return):
                    scan_expr(s.value)
                elif isinstance(s, A.ExprStmt):
                    scan_expr(s.expr)
                elif isinstance(s, A.If):
                    scan_expr(s.test)
                    scan_stmts(s.then)
                    scan_stmts(s.orelse)
                elif isinstance(s, A.While):
                    scan_expr(s.test)
                    scan_stmts(s.body)
                    scan_stmts(s.orelse)
                elif isinstance(s, A.For):
                    scan_expr(s.iter)
                    for a in s.range_args:
                        scan_expr(a)
                    scan_stmts(s.body)
                    scan_stmts(s.orelse)
                elif isinstance(s, A.IndexAssign):
                    scan_expr(s.target)
                    scan_expr(s.value)
                elif isinstance(s, A.AttrAssign):
                    scan_expr(s.obj)
                    scan_expr(s.value)
                elif isinstance(s, A.AugAssign):
                    scan_expr(s.target)
                    scan_expr(s.value)
                elif isinstance(s, A.MultiAssign):
                    scan_expr(s.value)
                elif isinstance(s, A.Try):
                    scan_stmts(s.body)
                    scan_stmts(s.handler)
                    scan_stmts(s.else_body)
                    scan_stmts(s.finally_body)
                    for _types, _bind, hbody in s.extra_handlers:
                        scan_stmts(hbody)
                elif isinstance(s, A.With):
                    scan_expr(s.expr)
                    scan_stmts(s.body)
                elif isinstance(s, A.Raise):
                    scan_expr(s.value)
                elif isinstance(s, A.Match):
                    scan_expr(s.subject)
                    for _pat, guard, case_body in s.cases:
                        scan_expr(guard)
                        scan_stmts(case_body)
                elif isinstance(s, A.YieldStmt):
                    scan_expr(s.value)

        scan_stmts(body)
        return hints

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
            self._correct_true_division_return(sig, f)
            if self._needs_return_element_kind(sig):
                self._fill_return_element_kind(sig, f, f.name)
                continue
            if sig.ret_type is not None or sig.ret_tuple is not None:
                continue
            ty = self._infer_return_type(f, f.name)
            if ty is not None:
                sig.ret_type = ty
                # The inference gives the SHAPE ("it returns a list") but no
                # element kind, so a caller of an unannotated
                # `def f(): return [[1, 2]]` read the inner lists as ints and
                # printed pointers. Same fill the bare-annotation case gets.
                if self._needs_return_element_kind(sig):
                    self._fill_return_element_kind(sig, f, f.name)
        for c in self.mod.classes:
            for m in c.methods:
                sig = self.classes[c.name].methods[m.name]
                self._correct_true_division_return(sig, m)
                if self._needs_return_element_kind(sig):
                    self._fill_return_element_kind(sig, m, f"{c.name}.{m.name}", c.name)
                    continue
                if (
                    sig.ret_type is not None
                    or sig.ret_tuple is not None
                    or sig.returns_self
                ):
                    continue
                ty = self._infer_return_type(m, f"{c.name}.{m.name}", c.name)
                if ty is not None:
                    sig.ret_type = ty
                    if self._needs_return_element_kind(sig):
                        self._fill_return_element_kind(
                            sig, m, f"{c.name}.{m.name}", c.name
                        )

    def _correct_true_division_return(self, sig, fn) -> None:
        """Correct an `int` return type on a function whose every `return`
        yields a TRUE DIVISION.

        `/` yields a float in Python 3 no matter what its operands are, so
        `def half(x): return x / 2` returns a float -- but the pre-sema return
        inference merges the operand types and concludes `int`, and the caller
        then printed the returned double's raw bits as a pointer-sized integer.
        Runs before the element-kind and unannotated-inference passes below,
        both of which skip a signature that already has a type.

        Deliberately requires EVERY return to be a division: a function with
        one `/` return and one int return is a genuine mix this must not
        silently retype.
        """
        _rt = getattr(sig, "ret_type", None)
        if not (isinstance(_rt, tuple) and _rt and _rt[0] == "int"):
            return
        _rets: list = []
        self._collect_returns(fn.body, _rets)
        if not _rets:
            return
        for _r in _rets:
            _v = getattr(_r, "value", None)
            if not (isinstance(_v, A.BinOp) and _v.op == "/"):
                return
        sig.ret_type = ("float", None, None)

    def _learn_return_element_kind(self, value, scope: Scope) -> None:
        """Record the element kind of a returned container onto the enclosing
        function's signature, when the signature has none.

        The pre-passes that infer return types run before any body is checked,
        so they can only read kinds off literals. HERE the full scope is
        available -- a local list built by appends of arbitrary expressions has
        a known element kind by the time its `return` is reached. Nothing else
        about the signature is touched, and a signature that already knows its
        element kind is left alone.
        """
        _fn_name = self.in_function
        if _fn_name is None:
            return
        _sig = self.funcs.get(_fn_name)
        if _sig is None and "__" in _fn_name:
            _cls_part, _, _m_part = _fn_name.rpartition("__")
            _cs = self.classes.get(_cls_part)
            if _cs is not None:
                _sig = _cs.methods.get(_m_part)
        if _sig is None:
            return
        _rt = getattr(_sig, "ret_type", None)
        if not (isinstance(_rt, tuple) and _rt and _rt[0] == "list"):
            return
        if _rt[1] not in (None, "any", "int"):
            return  # already knows something concrete
        if A.expr_type(value) != "list":
            return
        _el = self._list_el_type(value, scope)
        if _el in ("", "int", "any", "?"):
            return
        _sig.ret_type = ("list", _el, _rt[2] if len(_rt) > 2 else None)
        if _el == "tuple":
            _slots = self._list_el_tuple_types(value, scope)
            if _slots:
                _sig.ret_list_tuple_types = list(_slots)

    def _detect_closure_factories(self) -> None:
        """Mark every function that returns a closure with a "closure" return
        type, so a call on it -- in any position -- dispatches correctly.

        Syntactic: a function whose `return <name>` names something a
        `ClosureBind` in the same body created. Fills `_closure_factories` and
        `_closure_targets`.
        """
        def _has_bind(body: list, name: str) -> bool:
            for _s in body:
                if isinstance(_s, A.ClosureBind) and _s.func_name == name:
                    return True
            return False
        # LIFTED functions count too: `def a(x): def b(y): def c(z): ...` makes
        # `b` a lifted function that is itself a closure factory, and
        # `a(1)(2)(3)` needs each step's result typed.
        for f in self.mod.funcs:
            sig = self.funcs.get(f.name)
            if sig is None or (
                sig.ret_type is not None and sig.ret_type[0] != "any"
            ):
                continue
            for _s in f.body:
                if (
                    isinstance(_s, A.Return)
                    and _s.value is not None
                    and isinstance(_s.value, A.Name)
                    and _has_bind(f.body, _s.value.name)
                ):
                    sig.ret_type = ("closure", None, None, None, None)
                    self._closure_factories.add(f.name)
                    self._closure_targets[f.name] = _s.value.name
                    break

    def _pack_closure_vararg_call(self, call, funcs_by_name: dict) -> None:
        """Pack a closure call's surplus positional arguments into the single
        list parameter its `*args` target expects.

        `def mk(f): def go(*a): ...; return go` then `g = mk(d); g(4, 5)`: the
        call goes through a closure VALUE, so there was no signature to bind
        against and `go` received two raw arguments where it expects one packed
        list -- the call silently did nothing. The target is known here (see
        `_closure_targets`), so bind by hand.

        A lifted function's free variables were PREPENDED to its parameter list,
        and the closure object supplies those itself at the call, so the
        user-visible parameters start after them.
        """
        _tgt_name = self._closure_var_targets.get(call.func)
        if _tgt_name is None or getattr(call, "_closure_packed", False):
            return
        _tgt = funcs_by_name.get(_tgt_name)
        if _tgt is None or not getattr(_tgt, "vararg", None):
            return
        _nfree = len(getattr(_tgt, "free_vars", []) or [])
        _fixed = list(_tgt.params)[_nfree:]
        # `params` INCLUDES the vararg (and kwarg) name; those are not fixed
        # positional slots, so drop them or every count below is off by one.
        for _tail in (getattr(_tgt, "vararg", None), getattr(_tgt, "kwarg", None)):
            if _tail and _fixed and _fixed[-1] == _tail:
                _fixed.pop()
        if len(call.args) < len(_fixed):
            return
        _packed = list(call.args[len(_fixed):])
        if len(_packed) == 1 and isinstance(_packed[0], A.ListLit):
            return  # already packed
        call.args = list(call.args[: len(_fixed)]) + [
            A.ListLit(elems=_packed, pos=call.pos)
        ]
        call._closure_packed = True  # type: ignore[attr-defined]

    def _restamp_module_call_types(self) -> None:
        """Re-stamp element kinds onto module-body call nodes after every
        function body has been checked.

        Function bodies are checked AFTER the module body -- they have to be,
        since they read module-level globals -- so a return kind only learned
        while checking a body arrives too late for the call sites that consumed
        it. This walks those call nodes and copies the now-known kinds across.
        Deliberately a RE-STAMP and not a re-check: re-checking would re-run
        `_bind_args` over already-normalized arguments and repack a `*args`
        list inside itself.
        """
        for _call in _walk_call_sites(self.mod.body):
            _sig = self.funcs.get(_call.func)
            if _sig is None:
                continue
            _rt = getattr(_sig, "ret_type", None)
            if not (isinstance(_rt, tuple) and _rt and _rt[0] == "list"):
                continue
            if _rt[1] in (None, "any", "int"):
                continue
            if A.expr_type(_call) != "list":
                continue
            if getattr(_call, "list_el_type", "int") not in ("int", "any", ""):
                continue
            _call.list_el_type = _rt[1]  # type: ignore[attr-defined]
            if _rt[1] == "tuple" and getattr(_sig, "ret_list_tuple_types", None):
                _call.tuple_elem_types = list(_sig.ret_list_tuple_types)  # type: ignore

    def _needs_return_element_kind(self, sig) -> bool:
        """True for a signature annotated with a bare CONTAINER -- `-> list`,
        `-> dict` -- and therefore carrying no element/value kind."""
        _rt = getattr(sig, "ret_type", None)
        if not isinstance(_rt, tuple) or len(_rt) < 3:
            return False
        # A bare `list` resolves to element kind "any" (not None) -- that is
        # what "no element kind was written" looks like after resolution. An
        # explicit `list[object]` resolves the same way, so the fill below only
        # narrows when EVERY return agrees on one concrete literal kind, which
        # a genuinely heterogeneous `list[object]` never will.
        if _rt[0] == "list":
            return _rt[1] in (None, "any")
        if _rt[0] == "dict":
            return _rt[2] in (None, "any")
        return False

    def _fill_return_element_kind(self, sig, fn, qualname: str, cls_name=None) -> None:
        """Fill in the element/value kind of a bare `-> list` / `-> dict`
        annotation by scanning the function's own `return` statements.

        A bare container annotation states the SHAPE but not what is in it, and
        every consumer downstream needs the element kind: a caller's
        `sorted(f())` over a list of pairs reprs them as raw POINTERS without
        it, and a list-of-tuples with no slot shape crashes outright. The
        annotation is more specific than nothing, so it is kept -- only the
        missing half is inferred, using the same body scan that an entirely
        unannotated function already gets.
        """
        _rt: tuple = sig.ret_type
        _rets: list = []
        self._collect_returns(fn.body, _rets)
        if not _rets:
            return
        _kind: str | None = None
        _slots: list = []
        for _r in _rets:
            _rv = getattr(_r, "value", None)
            if isinstance(_rv, A.ListLit):
                _k = self._literal_shape_el_type(_rv)
                if _k is None and _rv.elems and all(
                    isinstance(_el, A.ListLit) for _el in _rv.elems
                ):
                    # A list OF LISTS: the element kind is "list".
                    _k = "list"
                elif _k is None and _rv.elems and all(
                    isinstance(_el, A.DictLit) for _el in _rv.elems
                ):
                    _k = "dict"
                if _k is None and _rv.elems:
                    # A list of tuples: its element kind is "tuple", and the
                    # per-slot shape is what the repr actually needs.
                    if all(isinstance(_el, A.TupleLit) for _el in _rv.elems):
                        _k = "tuple"
                        # Read the slot kinds straight off the LITERALS. This
                        # pre-pass runs before any expression has been checked,
                        # so `_common_tuple_slots` (which reads stamped types)
                        # sees nothing yet.
                        _sl: list = []
                        _shape_ok = True
                        for _el in _rv.elems:
                            _this: list = []
                            for _slot in _el.elems:
                                _lit = self._literal_arg_type(_slot)
                                if _lit is None:
                                    _shape_ok = False
                                    break
                                _this.append(_lit[0])
                            if not _shape_ok:
                                break
                            if not _sl:
                                _sl = _this
                            elif len(_sl) != len(_this):
                                _shape_ok = False
                                break
                            else:
                                for _si in range(len(_sl)):
                                    if _sl[_si] != _this[_si]:
                                        _sl[_si] = "any"
                        if not _shape_ok:
                            _sl = []
                        if _slots and _sl and _slots != _sl:
                            return
                        if _sl:
                            _slots = _sl
            elif isinstance(_rv, A.DictLit):
                # The dict's VALUE kind: a uniform literal kind, or -- what a
                # dispatch table actually holds -- a callable.
                _k = self._literal_shape_value_type(_rv)
                if _k is None and _rv.values:
                    _cvs: str | None = None
                    for _dv in _rv.values:
                        _ck = self._callable_type_of(_dv, Scope())
                        if _ck is None:
                            _cvs = None
                            break
                        if _cvs is None:
                            _cvs = _ck
                        elif _cvs != _ck:
                            _cvs = "callable:any"
                    _k = _cvs
            elif isinstance(_rv, A.Comprehension):
                _k = getattr(_rv, "list_el_type", None)
            elif isinstance(_rv, A.Name):
                # `result = []` ... `result.append(v)` ... `return result` --
                # the standard build-and-return shape. The local's element kind
                # is only knowable from its appends, which is exactly what the
                # field version of this scan does for `self.xs.append(v)`.
                _k = self._appended_element_kind(fn.body, _rv.name)
            else:
                _k = None
            if _k is None:
                return  # one return with no knowable kind: infer nothing
            if _kind is None:
                _kind = _k
            elif _kind != _k:
                return  # returns disagree
        if _kind is None:
            return
        if _rt[0] == "dict":
            # A returned DICT's VALUE kind, same idea as a list's element kind.
            # `def make_ops(): return {'inc': lambda x: x + 1}` -- without it the
            # caller's `ops['inc']` reads a value of unknown kind, so
            # `ops['inc'](5)` was not recognized as a call at all.
            sig.ret_type = (_rt[0], _rt[1], _kind)
            return
        if _rt[0] != "list":
            return
        sig.ret_type = (_rt[0], _kind, _rt[2])
        if _kind == "tuple" and _slots:
            sig.ret_list_tuple_types = list(_slots)

    def _appended_element_kind(self, stmts: list, name: str) -> "str | None":
        """The common element kind appended to the local list `name` anywhere in
        `stmts`, or None if there are no appends or they disagree.

        Mirrors `_scan_field_assigns`'s `self.xs.append(v)` rule for a LOCAL
        list. Only literal-shaped values count: this runs in a pre-pass, before
        any expression has a stamped type.
        """
        seen: str | None = None
        for _s in self._walk_stmts_flat(stmts):
            if not (
                isinstance(_s, A.ExprStmt)
                and isinstance(_s.expr, A.MethodCall)
                and _s.expr.method == "append"
                and len(_s.expr.args) == 1
                and isinstance(_s.expr.obj, A.Name)
                and _s.expr.obj.name == name
            ):
                continue
            _lit = self._literal_arg_type(_s.expr.args[0])
            if _lit is None:
                # One append whose kind isn't statically knowable makes the
                # whole list unknown -- guessing from the others would be wrong
                # for a genuinely mixed list.
                return None
            if seen is None:
                seen = _lit[0]
            elif seen != _lit[0]:
                return None
        return seen

    def _walk_stmts_flat(self, stmts: list):
        """Every statement in `stmts` at any nesting depth."""
        for _s in stmts:
            yield _s
            for _attr in ("body", "then", "orelse", "handler", "else_body",
                          "finally_body"):
                _sub = getattr(_s, _attr, None)
                if isinstance(_sub, list):
                    for _inner in self._walk_stmts_flat(_sub):
                        yield _inner

    def _collect_returns(self, stmts: list, out: list) -> None:
        """Every `A.Return` with a value reachable in `stmts`, at any depth."""
        for _s in stmts:
            if isinstance(_s, A.Return) and getattr(_s, "value", None) is not None:
                out.append(_s)
            for _attr in ("body", "then", "orelse", "handler", "else_body",
                          "finally_body"):
                _sub = getattr(_s, _attr, None)
                if isinstance(_sub, list):
                    self._collect_returns(_sub, out)

    def _infer_return_type(self, fn: A.FuncDef, qualname: str, cls_name: str | None = None):
        """(ty, el, val) for every reachable `return` in `fn.body`, if all
        have a value and those values' types are statically knowable
        (`_literal_arg_type`, or a reference to one of `fn`'s parameters whose
        type is known) and agree -- else None. Helper for
        `_infer_unannotated_returns`. `cls_name` (methods only) lets a
        `return self.field[i]` resolve to the field's element type via
        `_collect_field_types`'s `field_el_types` (it runs before this pass)."""
        returns: list = []
        fn_body: list = fn.body
        self._collect_returns(fn_body, returns)
        if not returns:
            return None
        types: list = []
        for r in returns:
            if r.value is None:
                return None  # bare `return` mixed in: ambiguous
            lit = self._literal_arg_type(r.value)
            if (
                lit is None
                and cls_name is not None
                and isinstance(r.value, A.Subscript)
                and isinstance(r.value.obj, A.Attr)
                and isinstance(r.value.obj.obj, A.Name)
                and r.value.obj.obj.name == "self"
            ):
                fsig = self.classes.get(cls_name)
                fname = r.value.obj.name
                if fsig is not None and fsig.fields.get(fname) == "list":
                    el = fsig.field_el_types.get(fname)
                    if el is not None:
                        lit = (el, None, None, None)
            if lit is None and isinstance(r.value, A.Name) and r.value.name in fn.params:
                j = fn.params.index(r.value.name)
                fn_param_types_2: list = fn.param_types
                fn_defaults_2: list = fn.defaults
                annot = fn_param_types_2[j] if j < len(fn_param_types_2) else None
                resolved = self._resolve_annot(annot)  # type: ignore
                if resolved is not None:
                    lit = resolved
                elif j < len(fn_defaults_2) and fn_defaults_2[j] is not None:
                    # Same `=None` carries-no-type-info fix as elsewhere.
                    dty2 = "any" if A.is_none_expr(fn_defaults_2[j]) else A.expr_type(fn_defaults_2[j])
                    lit = (dty2, None, None, None, None)
                else:
                    lit = self.inferred_param_types.get(f"{qualname}:{j}")
            if (
                lit is None
                and cls_name is not None
                and isinstance(r.value, A.Subscript)
                and isinstance(r.value.obj, A.Attr)
                and isinstance(r.value.obj.obj, A.Name)
                and r.value.obj.obj.name == "self"
            ):
                # `return self.xs[i]` (e.g. a `def r(self, i): return
                # self.registers[i]` accessor) -- the field's already-known
                # element kind (from `_collect_field_types`, which runs
                # before this pass) becomes the return type, same as a
                # param reference does above. Bare-int subscripts (where the
                # field type is genuinely "int", same as the unresolved
                # sentinel) just fall through to the int default below, same
                # outcome either way.
                fname = r.value.obj.name
                fty = self._resolve_field_type(cls_name, fname)
                if fty in ("list", "dict"):
                    el = self._resolve_field_el(cls_name, fname)
                    if el != "int":
                        lit = (el, None, None, None)
            if (
                lit is None
                and cls_name is not None
                and isinstance(r.value, A.MethodCall)
                and isinstance(r.value.obj, A.Attr)
                and isinstance(r.value.obj.obj, A.Name)
                and r.value.obj.obj.name == "self"
                and r.value.method in ("get", "pop", "setdefault")
            ):
                # `return self.table.get(k, None)` where `self.table` is a
                # dict field. Use the dict field's value kind as the method
                # return type; if that value is itself a dict with unknown
                # schema, make its inner values opaque so `result["key"]`
                # remains dynamic rather than falling back to int.
                fname = r.value.obj.name
                fty = self._resolve_field_type(cls_name, fname)
                if fty == "dict":
                    el = self._resolve_field_el(cls_name, fname)
                    if el == "dict":
                        lit = ("dict", None, "any", None)
                    elif el != "int":
                        lit = (el, None, None, None)
            if (
                lit is None
                and cls_name is not None
                and isinstance(r.value, A.MethodCall)
                and isinstance(r.value.obj, A.Name)
                and r.value.obj.name == "self"
            ):
                # `return self.other_method(...)` should inherit the other
                # method's inferred/annotated return when it is already known.
                csig = self.classes.get(cls_name)
                msig = csig.methods.get(r.value.method) if csig is not None else None
                if msig is not None and msig.ret_type is not None:
                    lit = msig.ret_type  # type: ignore[assignment]
            if lit is None:
                return None
            entry = (lit[0], lit[1], lit[2])
            if entry not in types:
                types.append(entry)
        if len(types) == 1:
            return types[0]
        # Genuinely heterogeneous returns (e.g. one branch returns an int,
        # another a str): the function's static return type is "any". A
        # caller that only knows "any" cannot treat the value as any one
        # concrete kind -- ir_lower's return store choke point boxes the
        # concrete-this-branch value so the caller can recover the real
        # runtime kind, and the caller unboxes on read. Returning "any" here
        # (rather than None, which left `ret_type` at whatever a single-
        # branch-biased guess had set) is what makes that boxing fire and
        # keeps the caller from mistreating a boxed int cell as a raw str
        # pointer.
        if len(types) >= 2:
            return ("any", None, None)
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
        if isinstance(base, tuple):
            # A compound descriptor (e.g. `("list", "int")` from a 3-deep
            # annotation like `list[list[list[int]]]` -- _resolve_annot's own
            # "list"/"dict" branches only resolve one extra level of nesting
            # (`el_value_type` has no slot for a THIRD level), so the leaf
            # passed down here is the still-nested `(base, el)` pair itself,
            # not a plain string. Only the outer container kind is
            # recoverable at this depth; the innermost element type is opaque
            # ("any") rather than crashing on `_base.split(".")` below (base
            # would be a tuple, not a str).
            outer = base[0]
            if outer in ("list", "dict", "tuple", "set", "frozenset"):
                return "set" if outer == "frozenset" else outer
            return "any"
        # Cast away the "any"/"int" type that gen1's sema assigns to the
        # unannotated `base` parameter: all string comparisons below and the
        # f"instance:{_base}" f-string need str-typed operands so gen1's
        # codegen picks _runtime_str_eq (not raw pointer comparison) and
        # formats the class name as a string (not an int-to-str decimal).
        _base: str = base
        if _base in ("int", "str", "float"):
            return _base
        if _base == "bool":
            return "int"
        if _base in ("list", "dict", "tuple"):
            # A nested collection element/value (`dict[str, list[str]]`): every
            # value is an 8-byte pointer, so the container kind passes through.
            return _base
        if _base in ("set", "frozenset"):
            return "set"
        if _base == "object":
            return "any"
        if _base in self.classes:
            return f"instance:{_base}"
        # A capitalized external/imported class (`list[Token]`, `dict[str, Expr]`),
        # or a dotted reference to a class we do model (`list[argparse.Namespace]`).
        # Prefer the modeled class if the leaf matches one; otherwise fall back to
        # an opaque instance so attribute/method access on elements read out of
        # the container stays lenient (mirrors _resolve_annot's handling of a
        # bare external annotation).
        # Explicit `: list` / `: str` intermediates: str.split() returns a list
        # whose subscript [-1] is typed "int" by default (no element annotation
        # on the result list); without the `: str` cast the f"instance:{_leaf}"
        # f-string formats the pointer value as a decimal integer instead of the
        # class name string (the same root bug as the `list[Token]` garbage-addr
        # error — gen1 emits _emit_int_to_str for "int"-typed f-string slots).
        _parts: list = _base.split(".")
        _leaf: str = _parts[-1]
        if _leaf in self.classes:
            return f"instance:{_leaf}"
        if _leaf[:1].isupper():
            return f"instance:{_leaf}"
        return "int"

    def _resolve_annot(self, annot: tuple | None):
        """Turn a parser annotation descriptor (base, el) into
        (ty, el_type, value_type, tuple_types, el_value_type), or None if it
        doesn't constrain the type (so the caller falls back to default
        inference). `annot` is a (base, el) tuple or None."""
        if annot is None:
            return None
        # Explicitly-annotated subscript reads, not `base, el = annot`:
        # `annot`'s own parameter has no annotation, so it's typed "int" by
        # default (no literal default, no annotation to seed it). Unpacking
        # an "int"-typed tuple via TupleAssign falls back to `ets[i] if i <
        # len(ets) else "int"` per slot (sema can't see annot's real
        # per-slot shape), so `base` came out "int"-typed even though its
        # runtime value is always a real string. Every `base == "list"` /
        # `base == "dict"` / etc. string-equality check below then silently
        # compiled as a raw pointer comparison instead of _runtime_str_eq
        # (whose dispatch in _gen_compare requires lt0 in ("str", "any")) --
        # always false, since a heap/interned string pointer never equals a
        # different literal's address. That made _resolve_annot silently
        # fail to recognize ANY annotation base (including a bare `: dict`)
        # in the selfhosted binary specifically, while the Python-hosted
        # compiler (whose own `base, el = annot` line runs as real Python,
        # with no static-type pass to get confused) worked fine -- a
        # selfhost-only divergence, confirmed via matching minimal repros
        # compiled by both compilers and diffing the generated .asm.
        base: str = annot[0]
        el = annot[1]
        if base in ("int", "str", "float", "bool"):
            return ("int" if base == "bool" else base, None, None, None, None)
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
            if isinstance(el, tuple) and el[0] == "tuple":
                slot_types = [self._resolve_scalar_annot(b) for b in el[1]]
                return ("dict", None, "tuple", slot_types, None)
            if isinstance(el, tuple) and el[0] == "list":
                inner_el = self._resolve_scalar_annot(el[1])
                return ("dict", None, "list", None, inner_el)
            if isinstance(el, tuple) and el[0] == "dict":
                inner_val = self._resolve_scalar_annot(el[1])
                return ("dict", None, "dict", None, inner_val)
            return ("dict", None, self._resolve_scalar_annot(el), None, None)
        if base == "tuple":
            slot_types: list[str] = []
            if isinstance(el, list):
                for slot in el:
                    slot_base = slot[0] if isinstance(slot, tuple) else slot
                    slot_types.append(self._resolve_scalar_annot(slot_base))
            return ("tuple", None, None, slot_types, None)
        if base in ("set", "frozenset"):
            # A `set`-annotated value: type it as a set so membership and the
            # set methods (`add`/`discard`/`remove`/`update`) resolve, rather
            # than falling through to the int default.
            return ("set", None, None, None, None)
        if base == "outparam":
            # `outparam[int]`/`outparam[float]`: a raw pointer the CALLER
            # owns, only meaningful as a parameter on an exported
            # (`@access(Public)`/`@abi(...)`) function -- see
            # _check_funcdef_export_shape's enforcement and IndexAssign's
            # outparam-store handling below. `el` (the pointee kind) is
            # carried through el_type so `out.value = expr`/`out[0] = expr`
            # can validate/coerce against it.
            return ("outparam", el or "int", None, None, None)
        if base == "inparam":
            # `inparam[int]`/`inparam[float]`: a raw, read-only, caller-
            # owned ARRAY pointer -- the read-side counterpart of
            # `outparam[T]` above, same export-only enforcement and
            # pointee-kind tracking, but indexed by a real (not just
            # literal-0) expression -- see the Subscript-read handling and
            # ir_lower.py's pointer-arithmetic lowering.
            return ("inparam", el or "int", None, None, None)
        if base == "any":
            # An explicit opaque annotation (`object`, `Any`, or a genuine
            # multi-type union the parser collapsed to "any"): constrain the
            # value to the lenient "any" type rather than leaving it to default
            # to int. Lets a `-> str | list` method type its result usefully.
            return ("any", None, None, None, None)
        if base == "none":
            return None
        if base in self.classes or base in self._class_name_set:
            return (f"instance:{base}", None, None, None, None)
        # A dotted reference to a class we do model (`module.ClassName`, e.g.
        # `argparse.ArgumentParser`): match on the leaf so the annotation
        # resolves to the real class (with its known methods/fields) instead
        # of falling through to the opaque/external branch below, which would
        # silently break the inheritance chain for any subclass returned
        # through a base-class-annotated function.
        leaf = base.split(".")[-1]
        if leaf in self.classes or leaf in self._class_name_set:
            return (f"instance:{leaf}", None, None, None, None)
        # An external / imported class annotation (`Token`, `A.IntLit`,
        # `FuncInfo`). We can't see its methods or fields, so model it as an
        # opaque instance: attribute and method access against it are checked
        # leniently (see _check_expr's Attr / MethodCall handling). The leaf of
        # a dotted path is the class-ish name. lstrip("_") first so private-
        # looking names (argparse._SubParsersAction, _ArgGroup, ...) still
        # count as class-like -- leaf[:1] alone is "_", never upper, so this
        # branch silently fell through to "unconstrained" below for any such
        # annotation, leaving the parameter typed "int" by default (the same
        # default the comment two blocks up describes) and every method call
        # on it failing with "int has no method ...".
        if leaf.lstrip("_")[:1].isupper():
            return (f"instance:{leaf}", None, None, None, None)
        # A lowercase unknown name (a type alias we don't model) — don't
        # constrain; the body's usage decides what's legal.
        return None

    def _seed_param(self, scope: Scope, name: str, annot, default_expr, inferred=None, pos=None) -> None:
        """Add a parameter to `scope`, typing it from its annotation if
        present, otherwise from a literal default, otherwise from
        `inferred` (a (ty, el, val, tup) tuple from
        `_infer_unannotated_params`, or None), otherwise int."""
        resolved = self._resolve_annot(annot)
        if resolved is not None:
            # See the matching fix in _collect_field_types: subscript reads
            # with an explicit `ty: str`, not a tuple-unpack, so `ty == "..."`
            # checks below route through _runtime_str_eq reliably.
            ty: str = resolved[0]
            el = resolved[1]
            val = resolved[2]
            tup = resolved[3]
            elval = resolved[4]
            if ty == "list" and tup:
                # list[tuple[T1,T2,...]]: slot types go into el_tuple_types so
                # `for a, b in pairs` can type each target correctly.
                scope.add(name, ty, el_type=el, value_type=val,
                          el_tuple_types=tup, el_value_type=elval)
            else:
                scope.add(name, ty, el_type=el, value_type=val, tuple_types=tup,
                          el_value_type=elval)
            return
        if default_expr is not None:
            scope.add(name, A.expr_type(default_expr))
            return
        if inferred is not None:
            self._seed_param_from_inferred(scope, name, inferred)
            return
        # `no_implicit_any` extension: reaching here means the parameter has
        # no annotation, no literal default, no inferred type, and no usage
        # hint (all four already ruled out by the caller before this call) --
        # a genuinely opaque parameter that would otherwise silently seed
        # "int" as asmpython's generic unknown-sentinel.
        if self._ext_active("no_implicit_any") and self.in_function is not None:
            raise SemaError(
                f"parameter {name!r} has no annotation, default, or "
                f"inferrable usage -- its type cannot be determined",
                pos,
                ErrorCode.E_IMPLICIT_ANY_PARAM,
            )
        scope.add(name, "int")

    def _seed_param_from_inferred(self, scope: Scope, name: str, inferred: tuple) -> None:
        """Unpack an inferred (ty, el, val, tup) 4-tuple and seed `scope`."""
        ty, el, val, tup = inferred[0], inferred[1], inferred[2], inferred[3]
        scope.add(name, ty, el_type=el, value_type=val, tuple_types=tup)

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
        scope.outparam_el_types.update(g.outparam_el_types)
        scope.inparam_el_types.update(g.inparam_el_types)

    def _prescan_fv_types(self) -> None:
        """Pre-scan every function/method body for ClosureBind nodes.

        For each ClosureBind found, record the types of its captured free
        variables into self._fv_types[func_name] so the lifted function's scope
        is seeded with the correct types rather than defaulting to int.  Uses
        annotation-based inference only (no full type analysis), allowing this
        to run before the main analysis loops regardless of order.
        """
        def literal_shape_type(value: A.Expr) -> tuple[str, object, object] | None:
            """Cheap, annotation-free type guess for an unannotated local's
            RHS, used only as a fallback when no `x: T = ...` annotation
            exists. Literal-shape only (no full expression evaluation) -
            covers exactly the case that bit a real closure free-variable
            (codegen.py's `GP_REGS = (...)`, an unannotated tuple literal):
            without this, scan_closurebinds' fallback below silently typed
            it "int", which made a `dest not in GP_REGS` tuple-membership
            test downstream get compiled as a dict-membership test instead
            of a list/tuple linear scan - a real, very-broad-impact bug
            (the peephole pass containing this closure runs on every
            compile), not just a theoretical gap.
            """
            if isinstance(value, A.TupleLit):
                return ("tuple", None, None)
            if isinstance(value, A.ListLit):
                return ("list", value.el_type, None)
            if isinstance(value, A.DictLit):
                val_type = value.value_type
                for dv in value.values:
                    if (
                        isinstance(dv, A.Call)
                        and dv.func[:1].isupper()
                        and dv.func not in self.funcs
                        and dv.func not in self.classes
                    ):
                        val_type = "any"
                return ("dict", None, val_type)
            if isinstance(value, A.SetLit):
                return ("set", None, None)
            if isinstance(value, A.StrLit):
                return ("str", None, None)
            if isinstance(value, A.IntLit):
                return ("int", None, None)
            if isinstance(value, A.FloatLit):
                return ("float", None, None)
            if (
                isinstance(value, A.Call)
                and value.func[:1].isupper()
                and value.func not in self.funcs
                and value.func not in self.classes
            ):
                return ("any", None, None)
            if isinstance(value, A.Call) and value.func in self.classes:
                # `nested = SomeClass(...)` captured by an inner closure: the
                # free var is an instance, not the int unknown-sentinel. Was
                # unhandled (the two Call branches above deliberately EXCLUDE
                # `value.func in self.classes`), so scan_closurebinds fell
                # through to ("int", None, None) and the closure body's
                # `nested.method(...)` failed with "int has no method 'X'".
                # Confirmed via a minimal repro (`class Box: def show(self)`;
                # `def outer(): nested = Box(5); def inner(): nested.show()`)
                # and by portapy's frontend.py comprehension lowering, where
                # a `nested = _Lowerer(...)` captured by an `emit_nested`
                # closure hit exactly this.
                return (f"instance:{value.func}", None, None)
            return None

        def collect_annot_locals(stmts: list, acc: dict) -> None:
            for s in stmts:
                if isinstance(s, A.Assign):
                    annot = getattr(s, "annot", None)
                    if annot is not None:
                        resolved = self._resolve_annot(annot)
                        if resolved is not None and isinstance(s.target, str):
                            ty: str = resolved[0]
                            el = resolved[1]
                            val = resolved[2]
                            acc[s.target] = (ty, el, val)
                    elif isinstance(s.target, str) and s.target not in acc:
                        guessed = literal_shape_type(s.value)
                        if guessed is not None and guessed[0] == "dict" and guessed[2] == "int":
                            if isinstance(s.value, A.DictLit):
                                for dv in s.value.values:
                                    if isinstance(dv, A.Name) and dv.name in acc:
                                        if acc[dv.name][0] == "any":
                                            guessed = ("dict", None, "any")
                        if guessed is not None:
                            acc[s.target] = guessed
                elif isinstance(s, A.If):
                    collect_annot_locals(s.then, acc)
                    if s.orelse:
                        collect_annot_locals(s.orelse, acc)
                elif isinstance(s, A.While):
                    collect_annot_locals(s.body, acc)
                elif isinstance(s, A.For):
                    collect_annot_locals(s.body, acc)
                elif isinstance(s, A.Try):
                    collect_annot_locals(s.body, acc)
                    collect_annot_locals(s.handler, acc)
                    if s.else_body:
                        collect_annot_locals(s.else_body, acc)
                elif isinstance(s, A.With):
                    collect_annot_locals(s.body, acc)

        def scan_closurebinds(stmts: list, local_types: dict) -> None:
            for s in stmts:
                if isinstance(s, A.ClosureBind):
                    local_types[s.func_name] = ("closure", None, None)
                    fv_types: list = []
                    for fv in s.free_vars:
                        if fv in local_types:
                            fv_types.append(local_types[fv])
                        elif fv in self.global_scope.types:
                            ty = self.global_scope.types[fv]
                            el = self.global_scope.list_el_types.get(fv)
                            val = self.global_scope.dict_value_types.get(fv)
                            fv_types.append((ty, el, val))
                        else:
                            fv_types.append(("int", None, None))
                    self._fv_types[s.func_name] = fv_types
                elif isinstance(s, A.If):
                    scan_closurebinds(s.then, local_types)
                    if s.orelse:
                        scan_closurebinds(s.orelse, local_types)
                elif isinstance(s, A.While):
                    scan_closurebinds(s.body, local_types)
                elif isinstance(s, A.For):
                    scan_closurebinds(s.body, local_types)
                elif isinstance(s, A.Try):
                    scan_closurebinds(s.body, local_types)
                    scan_closurebinds(s.handler, local_types)
                    if s.else_body:
                        scan_closurebinds(s.else_body, local_types)
                elif isinstance(s, A.With):
                    scan_closurebinds(s.body, local_types)

        def build_param_types(params: list, param_types: list) -> dict:
            local_types: dict = {}
            for i, p in enumerate(params):
                annot = param_types[i] if i < len(param_types) else None
                resolved = self._resolve_annot(annot)
                if resolved is not None:
                    ty: str = resolved[0]
                    el = resolved[1]
                    val = resolved[2]
                    local_types[p] = (ty, el, val)
            return local_types

        for f in self.mod.funcs:
            if getattr(f, "is_lifted", False):
                continue
            local_types: dict = build_param_types(f.params, f.param_types)
            collect_annot_locals(f.body, local_types)
            scan_closurebinds(f.body, local_types)

        for c in self.mod.classes:
            for m in c.methods:
                mdeco: list[str] = getattr(m, "decorators", [])
                local_types: dict = {}
                if "staticmethod" not in mdeco:
                    local_types["self"] = (f"instance:{c.name}", None, None)
                start = 0 if "staticmethod" in mdeco else 1
                # Explicit `: list` intermediates: m is opaque to sema so
                # m.params / m.param_types / m.body return opaque "any".
                # Slicing or iterating an opaque value uses wrong codegen
                # path (dict ops instead of list ops), causing SIGSEGV.
                m_params_ps: list = m.params
                m_param_types_ps: list = m.param_types
                method_locals: dict = build_param_types(
                    m_params_ps[start:], m_param_types_ps[start:]
                )
                for k, v in method_locals.items():
                    local_types[k] = v
                m_body_ps: list = m.body
                collect_annot_locals(m_body_ps, local_types)
                scan_closurebinds(m_body_ps, local_types)

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

    def _mlang_resolve_config(self, e) -> "tuple[str, str, tuple, bool] | None":
        """Statically resolve `e` (an mlang Config expression) to
        `(exe, frontend, compile_args, infer_signatures)`. Only the
        built-in `ml.builtins.*` configs are recognized in this first
        version -- a user-authored `ml.Config(...)` literal isn't
        statically evaluated here (that would need a general
        constant-folding evaluator this compiler doesn't have); returns
        None for anything else, and the caller raises a clear SemaError."""
        # e is an Attr chain: ml.builtins.gcc.cpp -> Attr(Attr(Attr(Name(ml),
        # "builtins"), "gcc"), "cpp"). ml.builtins.rust is one level
        # shallower (no per-frontend sub-attribute -- there's exactly one
        # rustc config, unlike gcc's cpp/c split or nasm's win64/elf64
        # split).
        names: list[str] = []
        cur = e
        while isinstance(cur, A.Attr):
            names.append(cur.name)
            cur = cur.obj
        if not isinstance(cur, A.Name):
            return None
        names.append(cur.name)
        names.reverse()  # e.g. ["ml", "builtins", "gcc", "cpp"]
        if len(names) == 3 and names[1] == "builtins":
            if names[2] == "rust":
                return (
                    "rustc", "rust",
                    (
                        "--crate-type", "staticlib", "--emit", "obj",
                        "-C", "panic=abort", "-C", "opt-level=2",
                        "-o", "{out}", "{src}",
                    ),
                    True,
                )
            return None
        if len(names) != 4 or names[1] != "builtins":
            return None
        if names[2] == "gcc" and names[3] == "cpp":
            return ("g++", "cpp", ("-c", "-x", "c++", "{src}", "-o", "{out}"), True)
        if names[2] == "gcc" and names[3] == "c":
            return ("gcc", "c", ("-c", "-x", "c", "{src}", "-o", "{out}"), True)
        if names[2] == "nasm" and names[3] == "win64":
            return ("nasm", "asm", ("-f", "win64", "{src}", "-o", "{out}"), False)
        if names[2] == "nasm" and names[3] == "elf64":
            return ("nasm", "asm", ("-f", "elf64", "{src}", "-o", "{out}"), False)
        return None

    def _mlang_resolve_exports(self, e) -> "dict":
        """Statically resolve `exports={"name": Sig([...], "...")}` (an
        mlang Code(...) kwarg) into `{name: mlang_support.MlangFuncSig}`.
        `e` is None when the kwarg wasn't passed at all (returns {}).
        Only a literal dict-of-Sig(...)-calls is understood -- anything
        else raises a clear SemaError rather than silently resolving to
        no exports (the earlier, pre-this-fix behavior: exports= was
        parsed but never actually forwarded to _run_mlang_code at all,
        so a NASM Config with no signature-inference support had no way
        to declare any exported function)."""
        from . import mlang_support as _mlang

        if e is None:
            return {}
        if not isinstance(e, A.DictLit):
            raise SemaError(
                "mlang Code(...)'s exports= must be a dict literal of "
                "Sig(...) calls",
                getattr(e, "pos", None),
                ErrorCode.E_MLANG_INVALID_ARG,
            )
        result: dict = {}
        for k, v in zip(e.keys, e.values):
            if not isinstance(k, A.StrLit):
                raise SemaError(
                    "mlang exports= keys must be string literals", e.pos,
                    ErrorCode.E_MLANG_INVALID_ARG,
                )
            if not (isinstance(v, A.Call) and v.func == "Sig" and len(v.args) == 2):
                raise SemaError(
                    f"mlang exports={{{k.value!r}: ...}} value must be a "
                    f"Sig(arg_types, ret_type) call",
                    e.pos,
                    ErrorCode.E_MLANG_INVALID_ARG,
                )
            arg_types_node, ret_type_node = v.args
            if not isinstance(arg_types_node, A.ListLit) or not all(
                isinstance(a, A.StrLit) for a in arg_types_node.elems
            ):
                raise SemaError(
                    f"mlang Sig(...) for {k.value!r}: arg_types must be a "
                    f"list of string literals",
                    e.pos,
                    ErrorCode.E_MLANG_INVALID_ARG,
                )
            if not isinstance(ret_type_node, A.StrLit):
                raise SemaError(
                    f"mlang Sig(...) for {k.value!r}: ret_type must be a "
                    f"string literal",
                    e.pos,
                    ErrorCode.E_MLANG_INVALID_ARG,
                )
            result[k.value] = _mlang.MlangFuncSig(
                arg_types=tuple(a.value for a in arg_types_node.elems),
                ret_type=ret_type_node.value,
            )
        return result

    def _inject_mlang_if_needed(self) -> None:
        """If the module imports `asmpython.mlang`, find every
        `target = <alias>.Code(config, source[, exports=...])` assignment
        (module or function scope), shell out to the configured compiler
        via `mlang_support._run_mlang_code`, and stamp `target`'s static
        type as a `mlang:<uid>` marker (mirrors the `super:<Base>`/
        `instance:<Class>` marker-type pattern already used elsewhere --
        see A.MethodCall's own `mlang:` dispatch for how that marker is
        later consumed). `self.mlang_code_funcs[uid]` and
        `self.mlang_objects` are populated here; driver.py's link step
        reads the latter to append the compiled object(s) and force the
        gcc linker."""
        alias = None
        for stmt in self.mod.body:
            if isinstance(stmt, A.Import) and stmt.module in ("asmpython.mlang", "mlang"):
                alias = stmt.alias or stmt.module.rsplit(".", 1)[-1]
                break
            if isinstance(stmt, A.FromImport) and stmt.module in ("asmpython.mlang", "mlang"):
                # `from asmpython.mlang import Code, Config, builtins` --
                # Code(...) is called bare, not qualified: recognized as an
                # A.Call(func="Code", ...) instead of A.MethodCall. Not yet
                # supported in this first version (only the `import ... as
                # ml; ml.Code(...)` qualified form is) -- no alias to key
                # the scan on, so this import shape is silently inert
                # rather than half-working.
                return
        if alias is None:
            return

        from . import mlang_support as _mlang

        def _scan(stmts: list) -> None:
            for s in stmts:
                if (
                    isinstance(s, A.Assign)
                    and isinstance(s.value, A.MethodCall)
                    and isinstance(s.value.obj, A.Name)
                    and s.value.obj.name == alias
                    and s.value.method == "Code"
                    and len(s.value.args) >= 2
                ):
                    call = s.value
                    config = self._mlang_resolve_config(call.args[0])
                    if config is None:
                        raise SemaError(
                            "mlang Code(...)'s first argument must be a "
                            "built-in config (ml.builtins.gcc.cpp / "
                            "ml.builtins.gcc.c) -- user-authored Config(...) "
                            "literals aren't statically resolvable yet",
                            call.pos,
                            ErrorCode.E_MLANG_INVALID_ARG,
                        )
                    if not isinstance(call.args[1], A.StrLit):
                        raise SemaError(
                            "mlang Code(...)'s source argument must be a "
                            "string literal",
                            call.pos,
                            ErrorCode.E_MLANG_INVALID_ARG,
                        )
                    exe, frontend, compile_args, infer_signatures = config
                    source = call.args[1].value
                    exports_node = next(
                        (kv[1] for kv in call.kwargs if kv[0] == "exports"), None
                    )
                    exports = self._mlang_resolve_exports(exports_node)
                    try:
                        result = _mlang._run_mlang_code(
                            exe, frontend, compile_args, infer_signatures, source, exports
                        )
                    except _mlang.MlangError as exc:
                        raise SemaError(str(exc), call.pos, ErrorCode.E_MLANG_COMPILE_FAILED) from exc
                    uid = str(id(s))
                    self.mlang_code_funcs[uid] = result.funcs
                    self.mlang_objects.extend(result.objects)
                    # Stamp the Code(...) call's own inferred_type -- same
                    # mechanism sema.py's super() check uses
                    # (`e.inferred_type = f"super:{parent}"`), so
                    # A.expr_type(s.value) naturally returns this marker
                    # and _bind_name_from_value's `t = A.expr_type(value)`
                    # binds `target` to it with no further plumbing.
                    call.inferred_type = "mlang:" + uid
                for attr in ("body", "then", "orelse", "handler", "finally_body", "else_body"):
                    sub = getattr(s, attr, None)
                    if isinstance(sub, list):
                        _scan(sub)

        _scan(self.mod.body)
        for f in self.mod.funcs:
            _scan(f.body)
        for c in self.mod.classes:
            for m in c.methods:
                _scan(m.body)

    def _try_check_block(self, stmts: list, scope: "Scope", *, tolerate: bool = False) -> None:
        """Run `_check_block` and, in collect-errors mode, stash any SemaError
        instead of propagating it so analysis continues in other bodies.

        `tolerate=True` (only ever passed for an unreachable, merged-stdlib
        function/method body -- see the two body-check loops in `analyze()`)
        goes further: any error is fully DISCARDED, not collected/reported,
        regardless of whether `--all-errors` is active for the rest of the
        program. This body will never execute (whole-program compilation
        merges every stdlib function unconditionally, whether or not the
        program calls it -- see `program.py`'s `load_program`), so a
        construct it uses that the native compiler can't yet give meaning to
        (e.g. `collections.namedtuple`'s dynamic `type()`/`property()` use)
        must not block compiling programs that never call it.
        """
        if tolerate:
            saved_collect, saved_errs = self.collect_errors, self._collected_errors
            self.collect_errors, self._collected_errors = True, []
            try:
                self._check_block(stmts, scope)
            finally:
                self.collect_errors, self._collected_errors = saved_collect, saved_errs
            return
        if not self.collect_errors:
            self._check_block(stmts, scope)
            return
        try:
            self._check_block(stmts, scope)
        except SemaError as e:
            self._collected_errors.append(e)

    def _collect_gen_locals(self, stmts: list, exclude: set) -> list:
        """Collect all local variable names assigned in stmts, excluding those in exclude."""
        names: list = []
        seen: set = set()
        def walk(ss):
            for s in ss:
                if isinstance(s, A.Assign):
                    n = s.target
                    if isinstance(n, str) and n not in exclude and n not in seen:
                        names.append(n)
                        seen.add(n)
                elif isinstance(s, A.AugAssign):
                    n = s.target
                    if isinstance(n, str) and n not in exclude and n not in seen:
                        names.append(n)
                        seen.add(n)
                elif isinstance(s, A.TupleAssign):
                    for t in s.targets:
                        nm = t.name if isinstance(t, A.StarTarget) else (t.name if isinstance(t, A.Name) else None)
                        if nm and nm not in exclude and nm not in seen:
                            names.append(nm)
                            seen.add(nm)
                elif isinstance(s, A.For):
                    if isinstance(s.var, str) and s.var not in exclude and s.var not in seen:
                        names.append(s.var)
                        seen.add(s.var)
                    walk(s.body)
                elif isinstance(s, A.While):
                    walk(s.body)
                elif isinstance(s, A.If):
                    walk(s.then)
                    if s.orelse:
                        walk(s.orelse)
        walk(stmts)
        return names

    def _rename_locals_in_stmts(self, stmts: list, local_names: set) -> list:
        """Deep-copy stmts, replacing each local Name ref with Attr(self, name)."""
        def _self_name() -> A.Name:
            return A.Name(name="self", pos=A.SourcePos(0, 0))

        def fix_expr(e):
            if e is None:
                return None
            if isinstance(e, A.Name):
                if e.name in local_names:
                    return A.Attr(obj=_self_name(), name=e.name, pos=e.pos)
                return e
            if isinstance(e, A.IntLit) or isinstance(e, A.FloatLit) or isinstance(e, A.StrLit):
                return e
            if isinstance(e, A.BinOp):
                return A.BinOp(op=e.op, left=fix_expr(e.left), right=fix_expr(e.right), pos=e.pos)
            if isinstance(e, A.UnaryOp):
                return A.UnaryOp(op=e.op, operand=fix_expr(e.operand), pos=e.pos)
            if isinstance(e, A.Compare):
                return A.Compare(ops=e.ops, operands=[fix_expr(x) for x in e.operands], pos=e.pos)
            if isinstance(e, A.BoolOp):
                return A.BoolOp(op=e.op, left=fix_expr(e.left), right=fix_expr(e.right), pos=e.pos)
            if isinstance(e, A.Attr):
                return A.Attr(obj=fix_expr(e.obj), name=e.name, pos=e.pos)
            if isinstance(e, A.Subscript):
                return A.Subscript(obj=fix_expr(e.obj), index=fix_expr(e.index), pos=e.pos)
            if isinstance(e, A.Call):
                return A.Call(
                    func=e.func,
                    args=[fix_expr(a) for a in e.args],
                    pos=e.pos,
                    kwargs=[(k, fix_expr(v)) for k, v in (e.kwargs or [])],
                )
            if isinstance(e, A.MethodCall):
                return A.MethodCall(
                    obj=fix_expr(e.obj),
                    method=e.method,
                    args=[fix_expr(a) for a in e.args],
                    pos=e.pos,
                )
            if isinstance(e, A.IfExp):
                return A.IfExp(test=fix_expr(e.test), body=fix_expr(e.body), orelse=fix_expr(e.orelse), pos=e.pos)
            if isinstance(e, A.ListLit):
                return A.ListLit(elems=[fix_expr(x) for x in e.elems], pos=e.pos)
            if isinstance(e, A.TupleLit):
                return A.TupleLit(elems=[fix_expr(x) for x in e.elems], pos=e.pos)
            return e

        def fix_stmt(s):
            if isinstance(s, A.Assign):
                target = s.target
                rhs = fix_expr(s.value)
                if isinstance(target, str) and target in local_names:
                    return A.AttrAssign(
                        obj=_self_name(), name=target, value=rhs, pos=s.pos
                    )
                return A.Assign(target=target, value=rhs, pos=s.pos, annot=s.annot)
            if isinstance(s, A.AugAssign):
                target = s.target
                rhs = fix_expr(s.value)
                if isinstance(target, str) and target in local_names:
                    lhs = A.Attr(obj=_self_name(), name=target, pos=s.pos)
                    new_val = A.BinOp(op=s.op, left=lhs, right=rhs, pos=s.pos)
                    return A.AttrAssign(obj=_self_name(), name=target, value=new_val, pos=s.pos)
                return A.AugAssign(target=target, op=s.op, value=rhs, pos=s.pos)
            if isinstance(s, A.ExprStmt):
                return A.ExprStmt(expr=fix_expr(s.expr), pos=s.pos)
            if isinstance(s, A.Return):
                return A.Return(value=fix_expr(s.value), pos=s.pos)
            if isinstance(s, A.If):
                return A.If(
                    test=fix_expr(s.test),
                    then=[fix_stmt(x) for x in s.then],
                    orelse=[fix_stmt(x) for x in (s.orelse or [])],
                    pos=s.pos,
                )
            if isinstance(s, A.While):
                return A.While(
                    test=fix_expr(s.test),
                    body=[fix_stmt(x) for x in s.body],
                    pos=s.pos,
                )
            if isinstance(s, A.For):
                return A.For(
                    var=s.var,
                    range_args=[fix_expr(x) for x in (s.range_args or [])],
                    iter=fix_expr(s.iter),
                    body=[fix_stmt(x) for x in s.body],
                    pos=s.pos,
                    targets=list(s.targets),
                )
            if isinstance(s, A.YieldStmt):
                return A.YieldStmt(value=fix_expr(s.value), pos=s.pos)
            if isinstance(s, A.AttrAssign):
                return A.AttrAssign(obj=fix_expr(s.obj), name=s.name, value=fix_expr(s.value), pos=s.pos)
            if isinstance(s, A.Break) or isinstance(s, A.Continue) or isinstance(s, A.Pass):
                return s
            if isinstance(s, A.Raise):
                return A.Raise(value=fix_expr(s.value), pos=s.pos)
            return s

        return [fix_stmt(x) for x in stmts]

    def _transform_straightline_generator(self, f: A.FuncDef, cls_name: str, pos):
        """Desugar a generator whose body is a straight run of `yield`s into a
        factory plus a state-machine iterator class.

        `def g(): yield 1; yield 2` becomes a class holding `_state`, whose
        `__next__` is a chain of `if self._state == N:` arms -- each advancing the
        state and returning that yield's expression -- ending in
        `raise StopIteration()`. Parameters are stored as fields by `__init__`
        (so a yielded expression may reference them) exactly as the loop-based
        transform does.
        """
        params_no_self = list(f.params)
        param_types_no_self = list(f.param_types) if f.param_types else [None] * len(f.params)
        all_names = set(params_no_self)

        init_body: list = []
        for p in params_no_self:
            init_body.append(A.AttrAssign(
                obj=A.Name(name="self", pos=pos), name=p,
                value=A.Name(name=p, pos=pos), pos=pos,
            ))
        init_body.append(A.AttrAssign(
            obj=A.Name(name="self", pos=pos), name="_state",
            value=A.IntLit(value=0, pos=pos), pos=pos,
        ))
        init_func = A.FuncDef(
            name="__init__",
            params=["self"] + params_no_self,
            body=init_body,
            defaults=[None] + [None] * len(params_no_self),
            param_types=[None] + list(param_types_no_self),
            pos=pos,
        )
        iter_func = A.FuncDef(
            name="__iter__",
            params=["self"],
            body=[A.Return(value=A.Name(name="self", pos=pos), pos=pos)],
            defaults=[None],
            param_types=[None],
            pos=pos,
        )

        next_body: list = []
        for i, stmt in enumerate(f.body):
            arm: list = [
                A.AttrAssign(
                    obj=A.Name(name="self", pos=pos), name="_state",
                    value=A.IntLit(value=i + 1, pos=pos), pos=pos,
                ),
                A.Return(
                    value=self._rename_expr(stmt.value, all_names), pos=pos
                ),
            ]
            next_body.append(A.If(
                test=A.Compare(
                    ops=["=="],
                    operands=[
                        A.Attr(obj=A.Name(name="self", pos=pos), name="_state", pos=pos),
                        A.IntLit(value=i, pos=pos),
                    ],
                    pos=pos,
                ),
                then=arm,
                orelse=[],
                pos=pos,
            ))
        next_body.append(A.Raise(
            value=A.Call(func="StopIteration", args=[], pos=pos), pos=pos
        ))
        # `__next__` returns ONE YIELDED VALUE. A generator function's own return
        # type resolves to the SEQUENCE it produces (`-> list[int]`), so take the
        # element kind from it -- declaring `__next__` as returning the container
        # made the caller read a yielded int as a list pointer and segfault.
        _rt = f.ret_type
        if (
            isinstance(_rt, tuple) and len(_rt) >= 2
            and _rt[0] in ("list", "tuple", "set") and _rt[1]
        ):
            _next_ret = (_rt[1], None)
        elif isinstance(_rt, tuple) and _rt and _rt[0] not in ("list", "tuple", "set"):
            _next_ret = _rt
        else:
            _next_ret = ("int", None)
        next_func = A.FuncDef(
            name="__next__",
            params=["self"],
            body=next_body,
            defaults=[None],
            param_types=[None],
            ret_type=_next_ret,
            pos=pos,
        )
        cls = A.ClassDef(
            name=cls_name, parent=None,
            methods=[init_func, iter_func, next_func], pos=pos,
        )
        factory_func = A.FuncDef(
            name=f.name,
            params=params_no_self,
            body=[A.Return(
                value=A.Call(
                    func=cls_name,
                    args=[A.Name(name=p, pos=pos) for p in params_no_self],
                    pos=pos,
                ),
                pos=pos,
            )],
            defaults=list(f.defaults) if f.defaults else [None] * len(params_no_self),
            param_types=list(param_types_no_self),
            # The BARE class name, matching the loop-based transform: sema
            # normalises it to `instance:<cls>` itself, and pre-normalising here
            # produced a factory whose return type never resolved.
            ret_type=(cls_name, None),
            pos=pos,
            vararg=f.vararg,
            kwarg=f.kwarg,
        )
        return factory_func, cls

    def _transform_generator(self, f: A.FuncDef) -> "tuple[A.FuncDef, A.ClassDef] | None":
        """Transform a generator function into a factory + iterator class.

        Supports:
          - Pre-loop stmts (init code before the while/for loop)
          - A single while loop containing exactly one yield per iteration path
          - A single for loop with yield in body

        Returns (new_factory_func, iterator_class) or None if pattern not recognized.
        """
        pos = f.pos
        cls_name = f"_genobj_{f.name}"
        params_no_self = list(f.params)  # e.g. ["n"]
        param_types_no_self = list(f.param_types) if f.param_types else [None] * len(f.params)

        # Separate pre-loop stmts from the first while/for loop.
        pre_stmts: list = []
        loop_stmt = None
        loop_body: list = []
        post_stmts: list = []
        for s in f.body:
            if loop_stmt is None:
                if isinstance(s, A.While):
                    _sw: A.While = s
                    loop_stmt = s
                    loop_body = _sw.body
                elif isinstance(s, A.For):
                    _sf: A.For = s
                    loop_stmt = s
                    loop_body = _sf.body
                else:
                    pre_stmts.append(s)
            else:
                post_stmts.append(s)

        if loop_stmt is None:
            # No loop, but a generator all the same: a STRAIGHT-LINE body whose
            # statements are all `yield`. Each yield is one state of a tiny state
            # machine -- `__next__` returns the Nth expression and advances a
            # `_state` field, then raises StopIteration once past the last. That
            # is the general desugaring for this shape (any number of yields, any
            # expression, params referenced as fields), rather than a special
            # case for two-yield functions.
            #
            # Restricted to bodies of NOTHING BUT yields on purpose: a statement
            # between two yields would have to run in the right segment AND its
            # locals would have to survive between `__next__` calls as fields.
            # Anything else returns None and is left alone rather than silently
            # mis-sequenced.
            if f.body and all(isinstance(_s, A.YieldStmt) for _s in f.body):
                return self._transform_straightline_generator(f, cls_name, pos)
            return None  # No loop found

        # Check that the loop contains exactly one YieldStmt in its body.
        def has_yield(stmts):
            for s in stmts:
                if isinstance(s, A.YieldStmt):
                    return True
                if isinstance(s, A.If):
                    _s_if: A.If = s
                    _if_body: list = _s_if.then
                    _if_orelse: list = _s_if.orelse or []
                    if has_yield(_if_body) or has_yield(_if_orelse):
                        return True
                elif isinstance(s, A.While):
                    _s_while: A.While = s
                    _while_body: list = _s_while.body
                    _while_orelse: list = _s_while.orelse or []
                    if has_yield(_while_body) or has_yield(_while_orelse):
                        return True
                elif isinstance(s, A.For):
                    _s_for: A.For = s
                    _for_body: list = _s_for.body
                    _for_orelse: list = _s_for.orelse or []
                    if has_yield(_for_body) or has_yield(_for_orelse):
                        return True
            return False

        if not has_yield(loop_body):
            return None

        # Collect all local variable names (excluding params) for renaming.
        all_params = set(params_no_self)
        all_locals = self._collect_gen_locals(f.body, all_params)
        all_locals_set = set(all_locals)
        all_names = all_params | all_locals_set

        # --- Build __init__ ---
        # Params: self + original params
        init_params = ["self"] + params_no_self
        init_param_types = [None] + list(param_types_no_self)
        init_defaults = [None] + [None] * len(params_no_self)
        init_body: list = []
        # self.param = param for each param
        self_name = A.Name(name="self", pos=pos)
        for p in params_no_self:
            init_body.append(A.AttrAssign(obj=A.Name(name="self", pos=pos), name=p, value=A.Name(name=p, pos=pos), pos=pos))
        # self.local = 0 for each local
        for loc in all_locals:
            init_body.append(A.AttrAssign(obj=A.Name(name="self", pos=pos), name=loc, value=A.IntLit(value=0, pos=pos), pos=pos))

        init_func = A.FuncDef(
            name="__init__",
            params=init_params,
            body=init_body,
            defaults=init_defaults,
            param_types=init_param_types,
            pos=pos,
        )

        # --- Build __iter__ ---
        iter_func = A.FuncDef(
            name="__iter__",
            params=["self"],
            body=[A.Return(value=A.Name(name="self", pos=pos), pos=pos)],
            defaults=[None],
            param_types=[None],
            pos=pos,
        )

        # --- Build __next__ ---
        # Transform the loop body: rename locals to self.X, split at yield.
        # For while loop: while cond: body_before; yield val; body_after
        #   → __next__: if not (cond with self.X): raise StopIteration
        #                body_before; result = val; body_after; return result
        next_body: list = []

        # Run pre-loop statements (init assignments like `i = 0`) — but only
        # once. We'll handle this by renaming them to AttrAssign and putting
        # them in __init__ instead (already done above for explicit locals).
        # The pre-stmts that aren't assignments (e.g. function calls) need to
        # go in __next__ with a _done-guard. For now, skip pre-stmts since
        # the common case (i = 0) is handled in __init__.

        result_name = "_genresult"

        if isinstance(loop_stmt, A.While):
            # Loop-in-next: `while cond: <transformed body>; raise StopIteration`.
            # _gen_body_transform recursively replaces every `yield val` with:
            #   _genresult = val; <continuation>; return _genresult
            # where the continuation carries all stmts that must still run
            # (e.g. `i += 1` after the yield) before state is saved.
            next_body = self._make_stmt_list()
            next_body.append(A.While(
                test=self._rename_expr(loop_stmt.test, all_names),
                body=self._gen_body_transform(
                    loop_stmt.body, [], all_names, pos, result_name
                ),
                pos=pos,
            ))
            next_body.append(A.Raise(
                value=A.Call(func="StopIteration", args=[], pos=pos), pos=pos
            ))

        elif isinstance(loop_stmt, A.For):
            _for_loop: A.For = loop_stmt
            # Tuple-unpacking for-loop generators are not yet supported.
            if _for_loop.targets:
                return None
            loop_var = _for_loop.var
            if not loop_var:
                return None

            # Build the iter expression: range_args → range(...), else use iter.
            if _for_loop.iter is not None:
                iter_expr = _for_loop.iter
            elif _for_loop.range_args:
                iter_expr = A.Call(func="range", args=list(_for_loop.range_args), pos=pos)
            else:
                return None

            renamed_iter = self._rename_expr(iter_expr, all_names)

            # Store the materialised list and index on self in __init__.
            init_body.append(A.AttrAssign(
                obj=A.Name(name="self", pos=pos), name="_genlist",
                value=A.Call(func="list", args=[renamed_iter], pos=pos),
                pos=pos,
            ))
            init_body.append(A.AttrAssign(
                obj=A.Name(name="self", pos=pos), name="_idx",
                value=A.IntLit(value=0, pos=pos),
                pos=pos,
            ))

            # Loop-in-next: advance _idx BEFORE the body so that any
            # yield-induced return leaves _idx pointing at the next element.
            # _gen_body_transform with empty continuation handles nested yields.
            loop_body_prefix = [
                A.AttrAssign(
                    obj=A.Name(name="self", pos=pos),
                    name=loop_var,
                    value=A.Subscript(
                        obj=A.Attr(obj=A.Name(name="self", pos=pos), name="_genlist", pos=pos),
                        index=A.Attr(obj=A.Name(name="self", pos=pos), name="_idx", pos=pos),
                        pos=pos,
                    ),
                    pos=pos,
                ),
                A.AttrAssign(
                    obj=A.Name(name="self", pos=pos),
                    name="_idx",
                    value=A.BinOp(
                        op="+",
                        left=A.Attr(obj=A.Name(name="self", pos=pos), name="_idx", pos=pos),
                        right=A.IntLit(value=1, pos=pos),
                        pos=pos,
                    ),
                    pos=pos,
                ),
            ]
            _cmp_ops = self._make_stmt_list()
            _cmp_ops.append(A.Attr(obj=A.Name(name="self", pos=pos), name="_idx", pos=pos))
            _cmp_ops.append(A.Call(func="len", args=[
                A.Attr(obj=A.Name(name="self", pos=pos), name="_genlist", pos=pos),
            ], pos=pos))
            next_body = self._make_stmt_list()
            next_body.append(A.While(
                test=A.Compare(ops=["<"], operands=_cmp_ops, pos=pos),
                body=loop_body_prefix + self._gen_body_transform(
                    _for_loop.body, [], all_names, pos, result_name
                ),
                pos=pos,
            ))
            next_body.append(A.Raise(
                value=A.Call(func="StopIteration", args=[], pos=pos), pos=pos
            ))

        # `__next__` returns ONE YIELDED VALUE, but a generator function's own
        # return annotation describes the SEQUENCE it produces (`-> list[int]`),
        # so take the element kind from it. Declaring `__next__` as returning the
        # container makes the caller read a yielded scalar as a pointer -- that
        # was a live segfault in the straight-line transform and is latent here
        # for any generator that carries an explicit sequence annotation.
        _rt = f.ret_type
        if (
            isinstance(_rt, tuple) and len(_rt) >= 2
            and _rt[0] in ("list", "tuple", "set") and _rt[1]
        ):
            ret_type = (_rt[1], None)
        elif isinstance(_rt, tuple) and _rt and _rt[0] not in ("list", "tuple", "set"):
            ret_type = _rt
        else:
            ret_type = ("int", None)

        next_func = A.FuncDef(
            name="__next__",
            params=["self"],
            body=next_body,
            defaults=[None],
            param_types=[None],
            ret_type=ret_type,
            pos=pos,
        )

        # --- Build the iterator class ---
        cls = A.ClassDef(
            name=cls_name,
            parent=None,
            methods=[init_func, iter_func, next_func],
            pos=pos,
        )

        # --- Build the factory function (replaces original) ---
        # factory_body: return _genobj_FNAME(args...)
        factory_body = [
            A.Return(
                value=A.Call(
                    func=cls_name,
                    args=[A.Name(name=p, pos=pos) for p in params_no_self],
                    pos=pos,
                ),
                pos=pos,
            )
        ]
        factory_func = A.FuncDef(
            name=f.name,
            params=list(f.params),
            body=factory_body,
            defaults=list(f.defaults),
            param_types=list(param_types_no_self),
            ret_type=(cls_name, None),
            pos=pos,
            vararg=f.vararg,
            kwarg=f.kwarg,
        )

        return factory_func, cls

    def _rename_expr(self, e, local_names: set):
        """Replace Name(x) with Attr(self, x) for x in local_names."""
        if e is None:
            return None
        if isinstance(e, A.Name):
            _en: A.Name = e
            if _en.name in local_names:
                return A.Attr(obj=A.Name(name="self", pos=_en.pos), name=_en.name, pos=_en.pos)
            return e
        if isinstance(e, A.IntLit) or isinstance(e, A.FloatLit) or isinstance(e, A.StrLit):
            return e
        if isinstance(e, A.BinOp):
            return A.BinOp(op=e.op, left=self._rename_expr(e.left, local_names), right=self._rename_expr(e.right, local_names), pos=e.pos)
        if isinstance(e, A.UnaryOp):
            return A.UnaryOp(op=e.op, operand=self._rename_expr(e.operand, local_names), pos=e.pos)
        if isinstance(e, A.Compare):
            return A.Compare(ops=e.ops, operands=[self._rename_expr(x, local_names) for x in e.operands], pos=e.pos)
        if isinstance(e, A.BoolOp):
            return A.BoolOp(op=e.op, left=self._rename_expr(e.left, local_names), right=self._rename_expr(e.right, local_names), pos=e.pos)
        if isinstance(e, A.Attr):
            return A.Attr(obj=self._rename_expr(e.obj, local_names), name=e.name, pos=e.pos)
        if isinstance(e, A.Call):
            return A.Call(func=e.func, args=[self._rename_expr(a, local_names) for a in e.args], pos=e.pos,
                         kwargs=[(k, self._rename_expr(v, local_names)) for k, v in (e.kwargs or [])])
        if isinstance(e, A.MethodCall):
            return A.MethodCall(obj=self._rename_expr(e.obj, local_names), method=e.method,
                               args=[self._rename_expr(a, local_names) for a in e.args], pos=e.pos)
        if isinstance(e, A.Subscript):
            return A.Subscript(obj=self._rename_expr(e.obj, local_names), index=self._rename_expr(e.index, local_names), pos=e.pos)
        if isinstance(e, A.IfExp):
            return A.IfExp(test=self._rename_expr(e.test, local_names), body=self._rename_expr(e.body, local_names),
                          orelse=self._rename_expr(e.orelse, local_names), pos=e.pos)
        if isinstance(e, A.ListLit):
            return A.ListLit(elems=[self._rename_expr(x, local_names) for x in e.elems], pos=e.pos)
        if isinstance(e, A.TupleLit):
            return A.TupleLit(elems=[self._rename_expr(x, local_names) for x in e.elems], pos=e.pos)
        return e

    def _rename_stmts(self, stmts: list, local_names: set, pos) -> list:
        """Rename local variable references in stmts to self.X."""
        result = stmts[0:0]
        for s in stmts:
            if isinstance(s, A.Assign):
                rhs = self._rename_expr(s.value, local_names)
                if isinstance(s.target, str) and s.target in local_names:
                    result.append(A.AttrAssign(obj=A.Name(name="self", pos=pos), name=s.target, value=rhs, pos=s.pos))
                else:
                    result.append(A.Assign(target=s.target, value=rhs, pos=s.pos, annot=s.annot))
            elif isinstance(s, A.AugAssign):
                rhs = self._rename_expr(s.value, local_names)
                if isinstance(s.target, str) and s.target in local_names:
                    lhs = A.Attr(obj=A.Name(name="self", pos=pos), name=s.target, pos=s.pos)
                    new_val = A.BinOp(op=s.op, left=lhs, right=rhs, pos=s.pos)
                    result.append(A.AttrAssign(obj=A.Name(name="self", pos=pos), name=s.target, value=new_val, pos=s.pos))
                else:
                    result.append(A.AugAssign(target=s.target, op=s.op, value=rhs, pos=s.pos))
            elif isinstance(s, A.ExprStmt):
                result.append(A.ExprStmt(expr=self._rename_expr(s.expr, local_names), pos=s.pos))
            elif isinstance(s, A.If):
                result.append(A.If(
                    test=self._rename_expr(s.test, local_names),
                    then=self._rename_stmts(s.then, local_names, pos),
                    orelse=self._rename_stmts(s.orelse or [], local_names, pos),
                    pos=s.pos,
                ))
            elif isinstance(s, A.While):
                result.append(A.While(
                    test=self._rename_expr(s.test, local_names),
                    body=self._rename_stmts(s.body, local_names, pos),
                    pos=s.pos,
                ))
            elif isinstance(s, A.AttrAssign):
                result.append(A.AttrAssign(obj=self._rename_expr(s.obj, local_names), name=s.name, value=self._rename_expr(s.value, local_names), pos=s.pos))
            elif isinstance(s, A.Break) or isinstance(s, A.Continue) or isinstance(s, A.Pass):
                result.append(s)
            elif isinstance(s, A.Return):
                result.append(A.Return(value=self._rename_expr(s.value, local_names), pos=s.pos))
            else:
                result.append(s)
        return result

    def _make_stmt_list(self) -> list:
        r: list = []
        return r

    def _gen_replace_returns(self, stmts: list) -> list:
        """Rewrite every `return` in a generator body into `raise
        StopIteration()`, recursively through nested statement bodies.

        In a generator, `return` ends the iteration -- CPython raises
        StopIteration, which is exactly how the transformed loop already
        reports exhaustion. (A returned VALUE is only observable through
        `yield from`, so it is dropped.) The rewrite has to recurse because
        the return is typically inside a non-yielding branch, which
        `_gen_body_transform` copies through untouched.
        """
        out: list = stmts[0:0]
        for s in stmts:
            if isinstance(s, A.Return):
                out.append(A.Raise(
                    value=A.Call(func="StopIteration", args=[], pos=s.pos),
                    pos=s.pos,
                ))
            elif isinstance(s, A.If):
                out.append(A.If(
                    test=s.test,
                    then=self._gen_replace_returns(s.then),
                    orelse=self._gen_replace_returns(s.orelse or []),
                    pos=s.pos,
                ))
            elif isinstance(s, A.While):
                out.append(A.While(
                    test=s.test,
                    body=self._gen_replace_returns(s.body),
                    orelse=self._gen_replace_returns(s.orelse or []),
                    pos=s.pos,
                ))
            else:
                out.append(s)
        return out

    def _gen_body_transform(self, stmts: list, continuation, all_names, pos, result_name):
        """Transform a generator loop body for __next__.

        Replaces every `yield val` with:
            _genresult = val
            <renamed continuation stmts>
            return _genresult

        `continuation` is a list of RAW (unrenammed) stmts from the outer
        context that must execute after the yield point.  For every `If`
        that contains a nested yield, the non-yielding branch also receives
        the continuation so both paths complete the same "iteration work"
        before falling through to the outer while loop's next check.
        """
        def _hw(ss):
            for s in ss:
                if isinstance(s, A.YieldStmt):
                    return True
                if isinstance(s, A.If) and (_hw(s.then) or _hw(s.orelse or [])):
                    return True
            return False

        # A `return` anywhere in a generator's body ends the iteration, so
        # rewrite it to the same StopIteration raise the exhausted loop uses --
        # recursively, because it usually sits in a non-yielding `if` branch
        # (`if x < 0: return`), which the transform below copies verbatim.
        # Left alone, __next__ returned a bogus value that the caller appended
        # as a real element and then kept iterating.
        stmts = self._gen_replace_returns(stmts)

        result = stmts[0:0]
        for i, s in enumerate(stmts):
            local_remaining = stmts[i + 1:]
            full_cont = local_remaining + list(continuation)

            if isinstance(s, A.YieldStmt):
                result.append(A.Assign(
                    target=result_name,
                    value=self._rename_expr(s.value, all_names),
                    pos=pos,
                ))
                result.extend(self._rename_stmts(full_cont, all_names, pos))
                result.append(A.Return(
                    value=A.Name(name=result_name, pos=pos), pos=pos
                ))
                break

            elif isinstance(s, A.Return):
                # `return` inside a generator ENDS the iteration -- CPython
                # raises StopIteration (the return value, if any, is only
                # observable through `yield from`, so it's dropped here). This
                # is the same exhaustion signal the transformed loop already
                # raises when it runs out. Left as a plain `return`, __next__
                # handed back a bogus value that the caller appended as a real
                # element and then kept iterating: `take_until_neg` produced
                # [1, 2, 0, 3] instead of [1, 2].
                result.append(A.Raise(
                    value=A.Call(func="StopIteration", args=[], pos=pos),
                    pos=pos,
                ))
                break

            elif isinstance(s, A.If):
                then_hw = _hw(s.then)
                orelse_hw = _hw(s.orelse or [])
                if then_hw or orelse_hw:
                    renamed_full = self._rename_stmts(full_cont, all_names, pos)
                    new_then = (
                        self._gen_body_transform(s.then, full_cont, all_names, pos, result_name)
                        if then_hw
                        else self._rename_stmts(s.then, all_names, pos) + renamed_full
                    )
                    new_orelse = (
                        self._gen_body_transform(s.orelse or [], full_cont, all_names, pos, result_name)
                        if orelse_hw
                        else self._rename_stmts(s.orelse or [], all_names, pos) + renamed_full
                    )
                    result.append(A.If(
                        test=self._rename_expr(s.test, all_names),
                        then=new_then,
                        orelse=new_orelse,
                        pos=s.pos,
                    ))
                    break  # full_cont is injected inside branches
                else:
                    result.extend(self._rename_stmts([s], all_names, pos))
            else:
                result.extend(self._rename_stmts([s], all_names, pos))
        return result

    def _ext_active(self, name: str) -> bool:
        """True if the named compiler extension was passed via --ext."""
        return name in self.active_extensions

    def analyze(self) -> None:
        # Inject stdlib Assembly class if the user imported it, so the
        # constructor and method calls resolve through the normal class path.
        self._inject_assembly_class_if_needed()
        # Compile every asmpython.mlang Code(...) literal (shells out to the
        # configured external compiler) and stamp each one's assignment
        # target with a mlang:<uid> marker type, before any other analysis
        # needs to resolve `code.add(...)`-style calls against it.
        self._inject_mlang_if_needed()
        # Seed the class-NAME set before anything resolves an annotation (see
        # `_class_name_set`). Refreshed again after the generator transform
        # below, which synthesizes new `_genobj_*` classes.
        for _c0 in self.mod.classes:
            self._class_name_set.add(_c0.name)
        # Transform generator functions (functions with yield) into factory +
        # iterator class pairs before any other analysis.
        new_funcs: list = []
        new_classes: list = []
        for f in self.mod.funcs:
            def _has_yield(stmts):
                for s in stmts:
                    if isinstance(s, A.YieldStmt):
                        return True
                    for attr in ('body', 'then', 'orelse'):
                        sub = getattr(s, attr, None)
                        if sub and _has_yield(sub):
                            return True
                return False
            if _has_yield(f.body):
                result = self._transform_generator(f)
                if result is not None:
                    factory, cls = result
                    new_funcs.append(factory)
                    new_classes.append(cls)
                else:
                    new_funcs.append(f)  # unsupported pattern, leave as-is
            else:
                new_funcs.append(f)
        self.mod.funcs = new_funcs
        self.mod.classes = list(self.mod.classes) + new_classes
        for _c1 in new_classes:
            self._class_name_set.add(_c1.name)
        # `overload` extension pre-pass: group same-named module-level defs
        # where EVERY copy is @overload-marked into self.overload_sets,
        # before the main signature-collection loop runs -- that loop's
        # own redefinition guard (below) is what actually skips building
        # a single self.funcs[name] entry for these names, so this has to
        # happen first. A name with some (not all) copies marked
        # @overload, or with 2+ copies while the extension is inactive,
        # falls straight through to the existing E003 hard-error --
        # unchanged behavior.
        _overload_funcdef_ids: set = set()
        if self._ext_active("overload"):
            by_name: dict = {}
            for f in self.mod.funcs:
                by_name.setdefault(f.name, []).append(f)
            for name, group in by_name.items():
                if len(group) < 2:
                    continue
                if not all("overload" in getattr(g, "decorators", []) for g in group):
                    continue
                sigs: list = []
                for g in group:
                    r = self._resolve_annot(g.ret_type)
                    sigs.append(FuncSig(
                        name=g.name,
                        arity=len(g.params),
                        n_defaults=_count_defaults(g.defaults),
                        pos=g.pos,
                        ret_type=(r[0], r[1], r[2]) if r is not None else None,
                        param_names=list(g.params),
                        param_defaults=list(g.defaults),
                        param_types=self._resolve_param_types(g),
                        vararg=g.vararg,
                        kwarg=g.kwarg,
                        decorators=list(getattr(g, "decorators", [])),
                    ))
                self._check_overload_group_distinct(name, sigs, group[0].pos)
                self.overload_sets[name] = sigs
                # Rename each real FuncDef to its mangled symbol IN PLACE --
                # both codegen backends compile a function under its own
                # `.name` attribute directly, so this is what actually
                # makes each overload land at a distinct symbol. Sema-side
                # dispatch (above) reads the mangled name back via
                # _overload_symbol(name, sig) applied to the ORIGINAL name +
                # the matched FuncSig, so it must produce the identical
                # string this rename uses -- both derive it from the same
                # (original name, sig) pair via the one shared helper.
                for g, sig in zip(group, sigs):
                    g.name = _overload_symbol(name, sig)
                    _overload_funcdef_ids.add(id(g))

        # First pass: collect function signatures so forward references resolve.
        for f in self.mod.funcs:
            if id(f) in _overload_funcdef_ids:
                # Handled by the pre-pass above -- register only the FIRST
                # occurrence into self.funcs (so plain, non-dispatch-aware
                # code paths that read self.funcs[name] directly, e.g.
                # simple existence checks, still find *something* real; the
                # actual multi-signature dispatch reads overload_sets
                # instead, never this single entry, for these names) and
                # skip the ordinary redefinition guard for every copy.
                if f.name not in self.funcs:
                    r = self._resolve_annot(f.ret_type)
                    _raw_ret_base = f.ret_type[0] if f.ret_type else None
                    self.funcs[f.name] = FuncSig(
                        name=f.name,
                        arity=len(f.params),
                        n_defaults=_count_defaults(f.defaults),
                        pos=f.pos,
                        ret_type=(r[0], r[1], r[2]) if r is not None else None,
                        ret_list_tuple_types=(r[3] if r is not None and r[1] == "tuple" else None),
                        ret_inner_el_type=(r[4] if r is not None and r[1] in ("list", "dict") else None),
                        param_names=list(f.params),
                        param_defaults=list(f.defaults),
                        param_types=self._resolve_param_types(f),
                        vararg=f.vararg,
                        kwarg=f.kwarg,
                        ret_tuple=(r[3] if r is not None and r[0] == "tuple" else None),
                        ret_bool=(_raw_ret_base == "bool"),
                        decorators=list(getattr(f, "decorators", [])),
                    )
                continue
            if f.name in self.funcs:
                if getattr(f, "is_lifted", False):
                    # A nested `def` is lifted to a module-level function keyed
                    # by its bare name (see parser.py's nested-def handling and
                    # A.ClosureBind) -- valid Python allows the same nested-def
                    # name in unrelated enclosing functions/methods (e.g. two
                    # methods each defining their own `def _do(): ...`), but
                    # this flat lifting scheme can't yet keep them apart.
                    raise SemaError(
                        f"nested function {f.name!r} collides with another "
                        f"nested/module function of the same name after being "
                        "lifted to module scope -- give it a distinct name "
                        "(asmpython does not yet mangle nested-function names "
                        "by enclosing scope)",
                        f.pos,
                        ErrorCode.E_REDEFINED_FUNC,
                    )
                raise SemaError(f"function {f.name!r} redefined", f.pos, ErrorCode.E_REDEFINED_FUNC)
            if f.name in BUILTINS and not getattr(f, "is_stdlib", False):
                raise SemaError(
                    f"cannot redefine builtin {f.name!r}",
                    f.pos,
                    ErrorCode.E_BUILTIN_REDEFINED,
                )
            r = self._resolve_annot(f.ret_type)  # type: ignore
            _raw_ret_base = f.ret_type[0] if f.ret_type else None
            self.funcs[f.name] = FuncSig(
                name=f.name,
                arity=len(f.params),
                n_defaults=_count_defaults(f.defaults),
                pos=f.pos,
                ret_type=(r[0], r[1], r[2]) if r is not None else None,
                ret_list_tuple_types=(r[3] if r is not None and r[1] == "tuple" else None),
                ret_inner_el_type=(r[4] if r is not None and r[1] in ("list", "dict") else None),
                param_names=list(f.params),
                param_defaults=list(f.defaults),
                param_types=self._resolve_param_types(f),
                vararg=f.vararg,
                kwarg=f.kwarg,
                ret_tuple=(r[3] if r is not None and r[0] == "tuple" else None),
                ret_bool=(_raw_ret_base == "bool"),
                decorators=list(getattr(f, "decorators", [])),
            )

        # A lightweight top-level prepass for tuple-return inference. This
        # lets `_scan_tuple_return` recognize module constants like
        # `MODE_REG` as ints before the full function-body pass.
        #
        # Deliberately NOT calling `_check_stmt`/`_check_expr` here (an
        # earlier version of this prepass did): those mutate shared AST
        # nodes in place as a side effect of real checking (`_bind_args`
        # rewrites a Call's `.args` list to fill in defaults/pack
        # `**kwargs`, `_clone_default_expr` allocates fresh nodes, etc.),
        # and this prepass runs over `self.mod.body` a second time before
        # the real `_try_check_block(self.mod.body, ...)` pass below --
        # so any call node with side-effecting args (e.g. a function
        # taking `**kwargs`) got double-expanded, corrupting its arg count
        # and raising a bogus "takes N argument(s), got N+1" error the
        # second (real) time it was checked. This only needs a syntax-only,
        # non-mutating read of simple module-level constant assignments, so
        # `_literal_arg_type` (already used for the same purpose elsewhere)
        # is enough -- it never touches call sites at all.
        self._tuple_scan_globals = Scope()
        self._tuple_scan_globals.add("__name__", "str")
        self._tuple_scan_globals.add("__file__", "str")
        self._tuple_scan_globals.add("__builtins__", "any")
        for stmt in self.mod.body:
            if not isinstance(stmt, A.Assign):
                continue
            lit = self._literal_arg_type(stmt.value)
            if lit is None:
                continue
            ty, el, val, tup = lit
            self._tuple_scan_globals.add(
                stmt.target, ty, el_type=el, value_type=val, tuple_types=tup
            )

        # Infer which functions return a tuple, and the shape of that tuple,
        # so call sites can unpack `q, r = f()`. Done before body analysis so
        # forward references and recursion still see the inferred shape.
        for f in self.mod.funcs:
            f_body_str: list = f.body
            sig = self.funcs.get(f.name)
            ets = list(sig.ret_tuple) if sig is not None and sig.ret_tuple else self._scan_tuple_return(f_body_str)
            if ets is not None:
                self.func_ret_tuple[f.name] = ets

        # Synthesise __init__ for @dataclass classes that don't define one.
        for c0 in self.mod.classes:
            c: A.ClassDef = c0
            if getattr(c, "is_dataclass", False):
                has_init = any(m.name == "__init__" for m in c.methods)
                if not has_init:
                    params: list = ["self"]
                    defaults: list = [None]
                    param_types: list = [None]  # self has no annotation
                    body_stmts: list = []
                    class_vars_list: list = c.class_vars
                    for fname, _fannot, fvalue in class_vars_list:
                        params.append(fname)
                        param_types.append(_fannot)  # carry class-var annotation
                        _func_nm = None
                        if isinstance(fvalue, A.Call):
                            _fv_call: A.Call = fvalue
                            _fv_call_func = _fv_call.func
                            if isinstance(_fv_call_func, str):
                                _func_nm = _fv_call_func
                        if isinstance(fvalue, A.Call) and _func_nm == "field":
                            _fv_call2: A.Call = fvalue
                            factory = None
                            _fv_call2_kw: list = _fv_call2.kwargs
                            for kn, kv in _fv_call2_kw:
                                if kn == "default_factory" and isinstance(kv, A.Name):
                                    _kv_nm: A.Name = kv
                                    factory = _kv_nm.name
                            if factory == "list":
                                defaults.append(A.ListLit(elems=[], pos=c.pos))
                            elif factory == "dict":
                                defaults.append(A.DictLit(keys=[], values=[], pos=c.pos))
                            elif factory == "set":
                                defaults.append(A.SetLit(elems=[], pos=c.pos))
                            else:
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
                        param_types=param_types,
                        pos=c.pos,
                    )
                    c.methods.insert(0, init_func)
                # Synthesise __repr__ for a @dataclass that doesn't define one:
                # `ClassName(f1=<repr>, f2=<repr>, ...)` (CPython uses repr() for
                # each field value, so a str field shows quotes). Built as a
                # plain str-concatenation Return so it flows through the normal
                # method type-check/lower path -- no runtime support needed.
                has_repr = any(m.name == "__repr__" for m in c.methods)
                if not has_repr and c.class_vars:
                    _rparts: list = [A.StrLit(value=c.name + "(", pos=c.pos)]
                    for _ri, (_rfn, _rann, _rval) in enumerate(c.class_vars):
                        if _ri > 0:
                            _rparts.append(A.StrLit(value=", ", pos=c.pos))
                        _rparts.append(A.StrLit(value=_rfn + "=", pos=c.pos))
                        _rattr = A.Attr(
                            obj=A.Name(name="self", pos=c.pos), name=_rfn, pos=c.pos
                        )
                        # Annotations are normalized descriptors `(base, param)`
                        # (see parser._parse_type_annotation), so read the base.
                        _rann_base = (
                            _rann[0]
                            if isinstance(_rann, (tuple, list)) and _rann
                            else _rann
                        )
                        if _rann_base == "bool":
                            # bool collapses to int here, so repr() would print
                            # 1/0 -- emit "True"/"False" via a ternary instead.
                            _rparts.append(
                                A.IfExp(
                                    test=_rattr,
                                    body=A.StrLit(value="True", pos=c.pos),
                                    orelse=A.StrLit(value="False", pos=c.pos),
                                    pos=c.pos,
                                )
                            )
                        else:
                            _rparts.append(A.Call(func="repr", args=[_rattr], pos=c.pos))
                    _rparts.append(A.StrLit(value=")", pos=c.pos))
                    _rexpr = _rparts[0]
                    for _rp in _rparts[1:]:
                        _rexpr = A.BinOp(op="+", left=_rexpr, right=_rp, pos=c.pos)
                    repr_func = A.FuncDef(
                        name="__repr__",
                        params=["self"],
                        body=[A.Return(value=_rexpr, pos=c.pos)],
                        defaults=[None],
                        param_types=[None],
                        pos=c.pos,
                    )
                    c.methods.append(repr_func)
                # Synthesise __eq__ for a @dataclass that doesn't define one:
                # field-by-field equality (CPython compares the tuple of
                # fields), instead of the default identity comparison that made
                # two equal dataclass values compare False. Returns int 1/0 --
                # the same shape the hand-written stdlib dunders use, and what
                # the `==`/`!=` instance dispatch above expects. `!=` reuses it
                # negated, so no separate __ne__ is needed.
                has_eq = any(m.name == "__eq__" for m in c.methods)
                if not has_eq and c.class_vars:
                    _eqx = None
                    for _efn, _eann, _eval in c.class_vars:
                        _ecmp = A.Compare(
                            ops=["=="],
                            operands=[
                                A.Attr(
                                    obj=A.Name(name="self", pos=c.pos),
                                    name=_efn,
                                    pos=c.pos,
                                ),
                                A.Attr(
                                    obj=A.Name(name="other", pos=c.pos),
                                    name=_efn,
                                    pos=c.pos,
                                ),
                            ],
                            pos=c.pos,
                        )
                        _eqx = (
                            _ecmp
                            if _eqx is None
                            else A.BoolOp(op="and", left=_eqx, right=_ecmp, pos=c.pos)
                        )
                    eq_func = A.FuncDef(
                        name="__eq__",
                        params=["self", "other"],
                        body=[
                            A.Return(
                                value=A.IfExp(
                                    test=_eqx,
                                    body=A.IntLit(value=1, pos=c.pos),
                                    orelse=A.IntLit(value=0, pos=c.pos),
                                    pos=c.pos,
                                ),
                                pos=c.pos,
                            )
                        ],
                        defaults=[None, None],
                        # `other` is annotated as this same class so its field
                        # reads resolve; annotations are (base, param) tuples.
                        param_types=[None, (c.name, None)],
                        ret_type=("int", None),
                        pos=c.pos,
                    )
                    c.methods.append(eq_func)

        # `enum` extension: collect enum type tables before class signatures
        # (Color.RED reads need this in place by the time any body is
        # checked, and enums don't reference classes or vice versa, so
        # ordering relative to the class-signature loop is otherwise free).
        for en in getattr(self.mod, "enums", []):
            if (
                en.name in self.enum_types
                or en.name in self.funcs
                or en.name in self.classes
                or en.name in BUILTINS
            ):
                raise SemaError(
                    f"enum name {en.name!r} collides with existing name",
                    en.pos,
                    ErrorCode.E_ENUM_REDEFINED,
                )
            self.enum_types[en.name] = {m_name: m_val for m_name, m_val in en.members}

        # `interface` extension: collect stub method tables before class
        # signatures, for the same reason enums are collected first --
        # `class X(interface=Name):` conformance-checking (below) needs
        # `self.interface_methods` populated before it runs.
        for iface in getattr(self.mod, "interfaces", []):
            if (
                iface.name in self.interface_methods
                or iface.name in self.funcs
                or iface.name in self.classes
                or iface.name in self.enum_types
                or iface.name in BUILTINS
            ):
                raise SemaError(
                    f"interface name {iface.name!r} collides with existing name",
                    iface.pos,
                    ErrorCode.E_INTERFACE_REDEFINED,
                )
            stub_table: dict = {}
            for stub in iface.methods:
                r = self._resolve_annot(stub.ret_type)
                stub_table[stub.name] = FuncSig(
                    name=stub.name,
                    arity=len(stub.params),
                    pos=stub.pos,
                    ret_type=(r[0], r[1], r[2]) if r is not None else None,
                    param_names=list(stub.params),
                )
            self.interface_methods[iface.name] = stub_table

        # Collect class signatures so methods + constructor calls resolve.
        for c in self.mod.classes:
            if (
                c.name in self.classes
                or c.name in self.funcs
                or c.name in self.enum_types
                or c.name in BUILTINS
            ):
                raise SemaError(
                    f"class name {c.name!r} collides with existing name", c.pos,
                    ErrorCode.E_CLASS_NAME_COLLISION,
                )
            sig = ClassSig(name=c.name, parent=c.parent, pos=c.pos)
            sig.is_final = getattr(c, "is_final", False)
            sig.is_sealed = getattr(c, "is_sealed", False)
            sig.sealed_permits = list(getattr(c, "sealed_permits", []) or [])
            sig.is_immutable = "immutable" in c.decorators
            if sig.is_immutable and not self._ext_active("immutable"):
                raise SemaError(
                    f"@immutable on class {c.name} is not supported -- "
                    f"asmpython's compiler-extension system was withdrawn "
                    f"(see archived/extensions/)",
                    c.pos,
                    ErrorCode.E_DECORATOR_WITHOUT_EXTENSION,
                )
            if getattr(c, "implements_interface", None) is not None and not self._ext_active("interface"):
                raise SemaError(
                    f"class {c.name}'s interface={c.implements_interface!r} "
                    f"is not supported -- asmpython's compiler-extension "
                    f"system was withdrawn (see archived/extensions/)",
                    c.pos,
                    ErrorCode.E_DECORATOR_WITHOUT_EXTENSION,
                )
            for fname, f_decos in getattr(c, "field_decorators", {}).items():
                if "immutable" in f_decos:
                    sig.immutable_fields.add(fname)
                if ("private" in f_decos or "protected" in f_decos) and not self._ext_active("access"):
                    raise SemaError(
                        f"@{'private' if 'private' in f_decos else 'protected'} on "
                        f"{c.name}.{fname} is not supported -- asmpython's "
                        f"compiler-extension system was withdrawn (see "
                        f"archived/extensions/)",
                        c.pos,
                        ErrorCode.E_DECORATOR_WITHOUT_EXTENSION,
                    )
                if "immutable" in f_decos and not self._ext_active("immutable"):
                    raise SemaError(
                        f"@immutable on {c.name}.{fname} is not supported -- "
                        f"asmpython's compiler-extension system was "
                        f"withdrawn (see archived/extensions/)",
                        c.pos,
                        ErrorCode.E_DECORATOR_WITHOUT_EXTENSION,
                    )
                if "private" in f_decos:
                    sig.field_access[fname] = "private"
                elif "protected" in f_decos:
                    sig.field_access[fname] = "protected"
            # `overload` extension: same pre-pass pattern as the module-
            # level one above, scoped to this one class's own methods.
            # Unlike module-level functions, plain method redefinition has
            # NO existing hard-error to preserve (confirmed: sig.methods[
            # m.name] = FuncSig(...) below just silently overwrites on a
            # second same-named method today) -- so this only needs to
            # rename/register the @overload-marked group; nothing to
            # "skip past a guard" for.
            if self._ext_active("overload"):
                m_by_name: dict = {}
                for m0 in c.methods:
                    m_by_name.setdefault(m0.name, []).append(m0)
                for mname, mgroup in m_by_name.items():
                    if len(mgroup) < 2:
                        continue
                    if not all("overload" in getattr(g, "decorators", []) for g in mgroup):
                        continue
                    msigs: list = []
                    for g in mgroup:
                        mr0 = self._resolve_annot(g.ret_type)
                        msigs.append(FuncSig(
                            name=g.name,
                            arity=len(g.params),
                            n_defaults=_count_defaults(g.defaults),
                            pos=g.pos,
                            ret_type=(mr0[0], mr0[1], mr0[2]) if mr0 is not None else None,
                            param_names=list(g.params),
                            param_defaults=list(g.defaults),
                            param_types=self._resolve_param_types(g),
                            vararg=g.vararg,
                            kwarg=g.kwarg,
                            decorators=list(getattr(g, "decorators", [])),
                        ))
                    self._check_overload_group_distinct(f"{c.name}.{mname}", msigs, mgroup[0].pos)
                    self.method_overload_sets[(c.name, mname)] = msigs
                    for g, msig in zip(mgroup, msigs):
                        g.name = _overload_symbol(mname, msig)
            for m in c.methods:
                deco: list[str] = getattr(m, "decorators", [])
                is_static = "staticmethod" in deco
                is_classm = "classmethod" in deco
                if ("private" in deco or "protected" in deco) and not self._ext_active("access"):
                    raise SemaError(
                        f"@{'private' if 'private' in deco else 'protected'} on "
                        f"{c.name}.{m.name} is not supported -- asmpython's "
                        f"compiler-extension system was withdrawn (see "
                        f"archived/extensions/)",
                        m.pos,
                        ErrorCode.E_DECORATOR_WITHOUT_EXTENSION,
                    )
                if "final" in deco and not self._ext_active("final"):
                    raise SemaError(
                        f"@final on {c.name}.{m.name} is not supported -- "
                        f"asmpython's compiler-extension system was "
                        f"withdrawn (see archived/extensions/)",
                        m.pos,
                        ErrorCode.E_DECORATOR_WITHOUT_EXTENSION,
                    )
                if "private" in deco:
                    sig.access[m.name] = "private"
                elif "protected" in deco:
                    sig.access[m.name] = "protected"
                if "final" in deco:
                    sig.final_methods.add(m.name)
                if not (is_static or is_classm):
                    if not m.params or m.params[0] != "self":
                        raise SemaError(
                            f"method {c.name}.{m.name!r} must take 'self' as its first parameter",
                            m.pos,
                            ErrorCode.E_MISSING_SELF_PARAM,
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
                _raw_mret_base = m.ret_type[0] if m.ret_type else None
                # Explicit `: list` intermediate: m.body is a direct
                # attribute read on m (external/opaque to sema), and passing
                # it straight into _scan_tuple_return/_method_returns_self
                # (both `stmts: list`-typed parameters) carried that opacity
                # through into _collect_returns' recursive walk, crashing
                # with a null-pointer dereference for any method at all.
                m_body: list = m.body
                sig.methods[m.name] = FuncSig(
                    name=m.name,
                    arity=len(m.params),
                    n_defaults=_count_defaults(m.defaults),
                    pos=m.pos,
                    ret_type=(mr[0], mr[1], mr[2]) if mr is not None else None,
                    ret_list_tuple_types=(mr[3] if mr is not None and mr[1] == "tuple" else None),
                    ret_inner_el_type=(mr[4] if mr is not None and mr[1] in ("list", "dict") else None),
                    param_names=list(m.params),
                    param_defaults=list(m.defaults),
                    # Methods never carried their resolved parameter types --
                    # only top-level functions did -- so anything keyed on them
                    # silently did nothing for a method or constructor.
                    param_types=self._resolve_param_types(m),
                    vararg=m.vararg,
                    kwarg=m.kwarg,
                    ret_tuple=(mr[3] if mr is not None and mr[0] == "tuple" else self._scan_tuple_return(m_body)),
                    decorators=list(getattr(m, "decorators", [])),
                    returns_self=mr is None and self._method_returns_self(m_body),
                    ret_bool=(_raw_mret_base == "bool"),
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
                            f"inheritance cycle involving {c.name!r}", c.pos,
                            ErrorCode.E_INHERITANCE_CYCLE,
                        )
                    seen.add(cur)
                    cur = self.classes[cur].parent

        # `final`/`sealed` extensions: enforce subclassing restrictions.
        # Both walk the DIRECT parent only (not the full ancestor chain) --
        # a class is final/sealed against its own immediate subclasses;
        # whether THOSE subclasses can themselves be further subclassed is a
        # separate, independent check against their own is_final/is_sealed.
        if self._ext_active("final") or self._ext_active("sealed"):
            for c in self.mod.classes:
                if c.parent is None or c.parent not in self.classes:
                    continue
                parent_sig = self.classes[c.parent]
                if self._ext_active("final") and parent_sig.is_final:
                    raise SemaError(
                        f"cannot subclass {c.parent!r}: it is a 'final class'",
                        c.pos,
                        ErrorCode.E_FINAL_CLASS_SUBCLASSED,
                    )
                if self._ext_active("sealed") and parent_sig.is_sealed:
                    if c.name not in parent_sig.sealed_permits:
                        raise SemaError(
                            f"cannot subclass {c.parent!r}: it is a 'sealed "
                            f"class' and {c.name!r} is not in its permits list",
                            c.pos,
                            ErrorCode.E_SEALED_SUBCLASS_NOT_PERMITTED,
                        )

        # `final` extension: enforce non-overridable methods. Walks the full
        # ancestor chain (not just the direct parent) so re-overriding two
        # levels down is also caught, e.g. Grandparent has @final def f(),
        # Parent doesn't mention f, Child def f() must still be rejected.
        if self._ext_active("final"):
            for c in self.mod.classes:
                sig = self.classes[c.name]
                cur = sig.parent
                seen = {c.name}
                while cur is not None and cur not in seen:
                    seen.add(cur)
                    ancestor_sig = self.classes.get(cur)
                    if ancestor_sig is None:
                        break
                    collision = sig.methods.keys() & ancestor_sig.final_methods
                    if collision:
                        raise SemaError(
                            f"cannot override {cur}.{sorted(collision)[0]}(): "
                            f"it is declared @final",
                            c.pos,
                            ErrorCode.E_FINAL_METHOD_OVERRIDDEN,
                        )
                    cur = ancestor_sig.parent

        # `interface` extension: every class declaring `implements_interface`
        # must implement every stub method (matching arity/return type),
        # via _resolve_method's existing parent-chain walk -- so a method
        # implemented on an ancestor class satisfies the contract too, not
        # just one declared directly on this exact class.
        if self._ext_active("interface"):
            for c in self.mod.classes:
                iface_name = getattr(c, "implements_interface", None)
                if iface_name is None:
                    continue
                stub_table = self.interface_methods.get(iface_name)
                if stub_table is None:
                    raise SemaError(
                        f"class {c.name} declares interface={iface_name!r}, "
                        f"but no such interface was declared",
                        c.pos,
                        ErrorCode.E_INTERFACE_UNKNOWN,
                    )
                for stub_name, stub_sig in stub_table.items():
                    resolved = self._resolve_method(c.name, stub_name)
                    if resolved is None:
                        raise SemaError(
                            f"class {c.name} does not implement "
                            f"{iface_name}.{stub_name}() required by its "
                            f"interface",
                            c.pos,
                            ErrorCode.E_INTERFACE_METHOD_MISSING,
                        )
                    impl_sig = resolved[1]
                    if impl_sig.arity != stub_sig.arity:
                        raise SemaError(
                            f"{c.name}.{stub_name}() has {impl_sig.arity} "
                            f"parameter(s), but interface {iface_name} "
                            f"declares {stub_sig.arity}",
                            c.pos,
                            ErrorCode.E_INTERFACE_METHOD_MISMATCH,
                        )
                    if (
                        stub_sig.ret_type is not None
                        and impl_sig.ret_type is not None
                        and stub_sig.ret_type[0] != impl_sig.ret_type[0]
                    ):
                        raise SemaError(
                            f"{c.name}.{stub_name}() returns "
                            f"{impl_sig.ret_type[0]!r}, but interface "
                            f"{iface_name} declares {stub_sig.ret_type[0]!r}",
                            c.pos,
                            ErrorCode.E_INTERFACE_METHOD_MISMATCH,
                        )

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

        # Infer instance-field types from `self.x = ...` so `obj.x` reads carry
        # the right static type. Done before any body is checked (top-level or
        # method) so every field read — including from module-level code — sees
        # the inferred field types. Also done before return-type inference
        # below, so `return self.field[i]` can resolve to the field's element
        # type (e.g. `def r(self, i): return self.registers[i]` on a
        # `self.registers: list[Trite]` field).
        self._collect_field_types()

        # Infer return types for functions/methods with no return annotation
        # and no inferred tuple-return shape, from their `return` statements
        # (using the parameter types just inferred above). See
        # `_infer_unannotated_returns`.
        self._infer_unannotated_returns()

        # Detect closure factories BEFORE any body is checked. The detection is
        # purely syntactic (does the function return a name bound by a
        # ClosureBind), so it needs nothing from the checking passes -- and
        # running it afterwards meant a DIRECT call on the result
        # (`adder(5)(10)`, as opposed to `f = adder(5); f(10)`) saw the factory
        # typed "any" and was rejected as not callable. The late pass below
        # still runs, to retype assignment call nodes.
        self._detect_closure_factories()

        self.global_scope = Scope()
        # Module dunders the runtime always provides.
        self.global_scope.add("__name__", "str")
        self.global_scope.add("__file__", "str")
        self.global_scope.add("__builtins__", "any")
        self._try_check_block(self.mod.body, self.global_scope)

        # Pre-scan all function/method bodies for ClosureBind nodes so that
        # free-variable types can be propagated into lifted function scopes.
        # Must run after global_scope is populated (above) and before free-var
        # params are prepended and function bodies are checked (below).
        self._prescan_fv_types()

        # Lifted (closure) functions: prepend captured free-variable names as
        # extra params so the body can reference them.  The outer function's
        # ClosureBind emits a closure list at runtime that passes these values.
        for f in self.mod.funcs:
            free_vars: list = getattr(f, "free_vars", [])
            if free_vars and getattr(f, "is_lifted", False):
                # Prepend free vars as params (before the original params).
                f.params = free_vars + list(f.params)
                f.param_types = [None] * len(free_vars) + list(f.param_types)
                f.defaults = [None] * len(free_vars) + list(f.defaults)

        # Which merged functions/methods are actually reachable from the
        # program's real entry point (mod.body)? Whole-program compilation
        # (program.py's load_program) merges EVERY top-level func/class from
        # every transitively-imported stdlib/project module unconditionally,
        # whether or not anything calls it -- computed here, before the body-
        # check loops below, so a merged-but-unreachable stdlib function with
        # a construct the native compiler can't check (e.g.
        # collections.namedtuple's dynamic type()/property() use) can be
        # tolerated (see _try_check_block's `tolerate` param) instead of
        # hard-failing the whole compile over code the program never runs.
        self._reachable_funcs, self._reachable_methods = _syntactic_reachable_names(self.mod)

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
            self._locked_params = self._compute_locked_params(f)
            free_vars: list = getattr(f, "free_vars", [])
            self._current_free_vars = set(free_vars) if self.in_lifted else set()
            self._locally_bound = set(f.params)
            scope = Scope()
            self._seed_globals_into(scope)
            n_fvs: int = len(free_vars)
            usage_hints: dict = self._param_usage_hints(f.params, f.body)
            # Explicit `: list` annotation: self._fv_types.get(...)'s result
            # type defaults wrong (unannotated dict.get), so len() on it fell
            # back to strlen() -- the same len()-on-opaque-attribute bug class
            # as f.param_types/f.defaults, but via a dict lookup this time.
            fv_type_list: list = self._fv_types.get(f.name, [])
            for i, p in enumerate(f.params):
                if i < n_fvs:
                    # Free-variable param: use the outer-scope type recorded
                    # by _prescan_fv_types() instead of defaulting to int.
                    if i < len(fv_type_list):
                        ty, el, val = fv_type_list[i]
                        scope.add(p, ty, el_type=el, value_type=val)
                    else:
                        scope.add(p, "int")
                else:
                    # `no_shadowing` extension, module-level case: a real
                    # (non-free-var) parameter sharing a name with a
                    # module-level global. Checked here rather than inside
                    # _require_assignable since params are seeded directly
                    # (scope.add), never routed through the rebind guard.
                    self._check_no_shadowing_global(p, f.pos)
                    # Explicit `: list` reads, not direct f.param_types /
                    # f.defaults attribute access: f is an external/opaque
                    # type to sema (A.FuncDef), so those attributes read
                    # "any"-typed despite always holding real lists. len()
                    # on an "any"-typed value falls back to strlen() (the
                    # generic "not a known container" case), so `len(
                    # f.param_types)` silently computed a garbage length by
                    # scanning the list's header bytes as a C string instead
                    # of reading its real length field -- which then let `i
                    # < len(f.param_types)` pass when it shouldn't have,
                    # tripping a real out-of-bounds list read on the next
                    # line. With explicit list types, len() dispatches
                    # through the real list-length path instead.
                    f_param_types: list = f.param_types
                    f_defaults: list = f.defaults
                    annot = f_param_types[i] if i < len(f_param_types) else None
                    default = f_defaults[i] if i < len(f_defaults) else None
                    inferred = self.inferred_param_types.get(f"{f.name}:{i}")
                    if (
                        annot is None
                        and default is None
                        and inferred is None
                        and p in usage_hints
                    ):
                        scope.add(p, usage_hints[p])
                        continue
                    self._seed_param(scope, p, annot, default, inferred, pos=f.pos)
            tolerate = getattr(f, "is_stdlib", False) and f.name not in self._reachable_funcs
            self._try_check_block(f.body, scope, tolerate=tolerate)
            self.in_function = None
            self.in_lifted = False
            self._locked_params = set()
            self._current_free_vars = set()
            self._locally_bound = set()

        # Method bodies: `self` is typed as the instance of its class.
        for c in self.mod.classes:
            for m in c.methods:
                if m.asm_body is not None:
                    continue  # raw-NASM method body, nothing to check
                self.in_function = f"{c.name}__{m.name}"
                self.current_class = c.name
                self._locked_params = self._compute_locked_params(m, skip_first=True)
                self._current_free_vars = set()
                self._locally_bound = set(m.params)
                scope = Scope()
                self._seed_globals_into(scope)
                # Explicit `: list` annotation: getattr(m, "decorators", [])
                # read as opaque "any" instead of "list" (m: A.FuncDef is
                # external/opaque to sema), so `"staticmethod" in mdeco`
                # routed through _gen_dict_in's container-membership dispatch
                # (treating mdeco's pointer as if it were a dict header)
                # instead of the real list-membership scan -- the source of
                # the _runtime_dict_contains-then-segfault crash whenever a
                # class with any method (decorated or not) was compiled.
                mdeco: list[str] = getattr(m, "decorators", [])
                # Explicit `: list` intermediate: m.params is an opaque
                # attribute read (m is external/opaque FuncDef), so slicing,
                # bool-testing, or indexing it directly uses wrong codegen.
                m_params_chk: list = m.params
                if "staticmethod" in mdeco:
                    # No implicit receiver: every parameter is a real argument.
                    start = 0
                elif "classmethod" in mdeco:
                    # First param is `cls` (opaque — asmpython has no class objs).
                    if m_params_chk:
                        scope.add(m_params_chk[0], "any")
                        self.classmethod_cls_param = m_params_chk[0]
                    start = 1
                else:
                    scope.add("self", f"instance:{c.name}")
                    start = 1
                m_param_types: list = m.param_types
                m_defaults: list = m.defaults
                usage_hints: dict = self._param_usage_hints(m_params_chk, m.body)
                for i, p in enumerate(m_params_chk[start:], start=start):
                    self._check_no_shadowing_global(p, m.pos)
                    annot = m_param_types[i] if i < len(m_param_types) else None
                    default = m_defaults[i] if i < len(m_defaults) else None
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
                    inferred = self.inferred_param_types.get(f"{c.name}.{m.name}:{i}")
                    if (
                        annot is None
                        and default is None
                        and inferred is None
                        and p in usage_hints
                    ):
                        scope.add(p, usage_hints[p])
                        continue
                    self._seed_param(scope, p, annot, default, inferred, pos=m.pos)
                m_body_chk: list = m.body
                tolerate = (
                    getattr(m, "is_stdlib", False)
                    and (c.name, m.name) not in self._reachable_methods
                )
                self._try_check_block(m_body_chk, scope, tolerate=tolerate)
                self.in_function = None
                self.current_class = None
                self.classmethod_cls_param = None
                self._locked_params = set()
                self._locally_bound = set()

        # Every body has now been checked, so any return kind learned from a
        # local's appends is available -- push it back onto the module-body call
        # sites that were typed before those bodies ran.
        self._restamp_module_call_types()

        # Raise all collected errors now that every body has been checked.
        if self._collected_errors:
            from .errors import MultiSemaError
            if len(self._collected_errors) == 1:
                raise MultiSemaError(self._collected_errors)
            raise MultiSemaError(self._collected_errors)

        # For functions that contain a ClosureBind and return that closure,
        # upgrade their FuncSig ret_type to "closure" so call sites get the
        # right type and codegen dispatches correctly.
        def _body_has_closure_bind(body: list, name: str) -> bool:
            for s in body:
                if isinstance(s, A.ClosureBind) and s.func_name == name:
                    return True
            return False
        closure_factories: set = self._closure_factories
        for f in self.mod.funcs:
            if getattr(f, "is_lifted", False):
                continue
            sig = self.funcs.get(f.name)
            # Proceed when the return type is unset OR was inferred as the
            # `any` fallback: `return <closure>` otherwise lands on `any`
            # (a closure is an opaque value to the scalar-return inference),
            # which then blocked this more-specific closure detection. A
            # closure return is a real, dispatchable kind -- prefer it.
            if sig is None or (
                sig.ret_type is not None and sig.ret_type[0] != "any"
            ):
                continue
            # Check if the function returns a name that is a ClosureBind.
            for s in f.body:
                if isinstance(s, A.Return) and s.value is not None:
                    if (
                        isinstance(s.value, A.Name)
                        and _body_has_closure_bind(f.body, s.value.name)
                    ):
                        sig.ret_type = ("closure", None, None, None, None)
                        closure_factories.add(f.name)
                        # Remember WHICH lifted function the closure wraps. A
                        # call through the closure otherwise has no signature to
                        # bind against, so a target declaring `*args` never got
                        # its arguments packed into the single list parameter it
                        # expects -- `g = mk(d); g(4, 5)` passed two raw
                        # arguments and the call did nothing at all.
                        self._closure_targets[f.name] = s.value.name
                        break
        # Re-type Call nodes that target closure factories, so a variable a
        # factory's result is assigned to (`add5 = make_adder(5)`) carries the
        # "closure" kind and its own later call dispatches correctly. Scans the
        # module body AND every function/method body -- an escaping closure is
        # most often assigned and called inside a function (`add5 = make_adder(
        # 5); add5(10)`), not at module scope, so a module-body-only scan missed
        # exactly the case that makes closures escape.
        def _retype_closure_calls(stmts: list) -> None:
            for s in stmts:
                if isinstance(s, A.Assign) and isinstance(s.value, A.Call):
                    if s.value.func in closure_factories:
                        s.value.inferred_type = "closure"
                        _tgt = self._closure_targets.get(s.value.func)
                        if _tgt is not None and isinstance(s.target, str):
                            self._closure_var_targets[s.target] = _tgt
                elif isinstance(s, A.For):
                    _retype_closure_calls(s.body)
                elif isinstance(s, A.If):
                    _retype_closure_calls(s.then)
                    _retype_closure_calls(s.orelse)
                elif isinstance(s, A.While):
                    _retype_closure_calls(s.body)
                elif isinstance(s, A.Try):
                    _retype_closure_calls(s.body)
                    _retype_closure_calls(s.handler)
                    _retype_closure_calls(s.else_body)
                    _retype_closure_calls(s.finally_body)
                elif isinstance(s, A.With):
                    _retype_closure_calls(s.body)
        _retype_closure_calls(self.mod.body)
        for f in self.mod.funcs:
            _retype_closure_calls(f.body)
        for c in self.mod.classes:
            for m in c.methods:
                _retype_closure_calls(m.body)
        # Now that closure variables are known, pack the arguments of calls
        # through them when the target declares `*args`. This runs as a pure
        # AST rewrite in this same late pass -- the calls were type-checked
        # before the closure targets were known, and re-checking them would
        # re-run `_bind_args` over already-normalized arguments.
        if self._closure_var_targets:
            _funcs_by_name = {_f.name: _f for _f in self.mod.funcs}
            for _stmts in (
                [self.mod.body]
                + [_f.body for _f in self.mod.funcs]
                + [_m.body for _c in self.mod.classes for _m in _c.methods]
            ):
                for _call in _walk_call_sites(_stmts):
                    self._pack_closure_vararg_call(_call, _funcs_by_name)

        # Hand resolved tables to codegen via the Module.
        self.mod.imported_modules = self.imported_modules
        self.mod.ffi_funcs = self.ffi_funcs
        self.mod.ffi_consts = self.ffi_consts
        self.mod.mlang_code_funcs = self.mlang_code_funcs
        self.mod.mlang_objects = self.mlang_objects
        # `overload` extension: whether this module actually uses it, so
        # driver.py can raise a clear "not supported on this backend"
        # error for --backend legacy instead of silently miscompiling --
        # see the module docstring note on codegen.py's own overload gap.
        self.mod.uses_overload = bool(self.overload_sets) or bool(self.method_overload_sets)
        # Codegen needs to look up methods by class chain for dispatch.
        self.mod.classes_sig = self.classes
        self.mod.funcs_sig = self.funcs

    # ---- helpers ------------------------------------------------------------

    def _check_block(self, stmts: list, scope: Scope) -> None:
        # Index-based so `_check_stmt` can splice extra statements (already
        # checked) immediately before `s` -- used by the `match` rewrite to
        # introduce a subject temp-variable assignment ahead of the `if`
        # chain it rewrites `s` into.
        i = 0
        while i < len(stmts):
            s = stmts[i]
            if self.collect_errors:
                # Per-statement recovery: a single bad statement (e.g. one
                # merged module's stray top-level expression) must not abort
                # every statement after it in the same block -- this block is
                # frequently the WHOLE merged module body, so one early error
                # would otherwise silently skip registering every later
                # global (including ones _materialize_value_imports already
                # successfully resolved), producing spurious "undefined
                # variable" errors far from the real fault.
                try:
                    extra = self._check_stmt(s, scope)
                except SemaError as e:
                    self._collected_errors.append(e)
                    i += 1
                    continue
            else:
                extra = self._check_stmt(s, scope)
            if extra:
                stmts[i:i] = extra
                i += len(extra)
            i += 1

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

    def _method_symbol_name(self, cls_name: str, method_name: str) -> str:
        """Return the mangled symbol name for a class method (matches codegen)."""
        return f"{cls_name}__{method_name}"

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
            elif el == "dict":
                inner = self._list_el_value_type(e.iter, child)
                child.add(e.var, "dict", value_type=inner if inner != "int" else "any")
            elif el == "list":
                inner = self._list_el_value_type(e.iter, child)
                child.add(e.var, "list", el_type=inner if inner != "int" else "any")
            else:
                child.add(e.var, el)
            return
        shape = self._list_el_tuple_types(e.iter, child) or list(
            getattr(e.iter, "tuple_elem_types", []) or []
        )
        flat = all(isinstance(t, str) for t in e.targets)
        for ti, nm in enumerate(self._flat_target_names(e.targets)):
            if flat and ti < len(shape):
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
        """Recognize `for a, b[, c...] in zip(A, B[, C...])` and
        `for i, (a, b[, c...]) in enumerate(zip(A, B[, C...]))`.

        Returns (idx_name_or_None, names_list, exprs_list) when `s` matches,
        otherwise None (so the caller falls back to ordinary handling).
        Names and exprs are parallel lists of N >= 2 items.
        """
        it = s.iter
        if it is None or not isinstance(it, A.Call):
            return None
        if it.func == "zip":
            n = len(it.args)
            if (
                n >= 2
                and len(s.targets) == n
                and all(isinstance(t, str) for t in s.targets)
            ):
                return (None, list(s.targets), list(it.args))
            return None
        if (
            it.func == "enumerate"
            and len(it.args) == 1
            and isinstance(it.args[0], A.Call)
            and it.args[0].func == "zip"
        ):
            z = it.args[0]
            n = len(z.args)
            if (
                n >= 2
                and len(s.targets) == n + 1
                and s.targets
            ):
                zip_vars: list = []
                for _i in range(1, len(s.targets)):
                    zip_vars.append(s.targets[_i])
                return (
                    s.targets[0],
                    zip_vars,
                    list(z.args),
                )
            return None
        return None

    def _iter_element_type(self, e, scope: Scope) -> str:
        """Element type yielded by iterating `e` (a list/str/dict/tuple/any)."""
        t: str = A.expr_type(e)
        if t == "list":
            el = self._list_el_type(e, scope)
            # Iterating a `list[Node]` yields INSTANCES of Node. `_list_el_type`
            # returns the annotation's bare class name ("Node"), which fails the
            # `instance:`-prefix gates on the loop variable's later uses (e.g.
            # `for e in self._queue: new.append(e)` -> E132, or a method call ->
            # E113). Normalize to `instance:<class>`, exactly as the subscript
            # read and `_dict_value_type` already do. Scalars/containers/`any`
            # pass through unchanged.
            return "any" if el == "?" else self._normalize_instance_type(el)
        if t in ("str", "dict", "set"):
            # Iterating a str yields its chars; a dict/set yields its keys --
            # all str-shaped values (see A.For's own generic set/dict
            # iteration, which types the loop var "str" for the same reason).
            # `set` was previously missing here, so a set-source comprehension
            # (`[len(x) for x in someset]`) typed its loop var "int" and
            # mis-lowered every use of it.
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
        if isinstance(e, A.Call):
            return getattr(e, "el_value_type", "int")
        if isinstance(e, A.MethodCall):
            return getattr(e, "el_value_type", "int")
        if isinstance(e, A.Attr):
            return getattr(e, "el_value_type", "int")
        if isinstance(e, A.BinOp):
            return getattr(e, "el_value_type", "int")
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
            #
            # Both spellings, because both are in use: a call whose RESULT is a
            # tuple carries `tuple_elem_types`, while a call whose result is a
            # LIST OF tuples carries `el_tuple_types` (what `sorted()` stamps).
            # Reading only the former silently dropped the shape of every
            # `sorted(list_of_pairs, ...)`, so assigning that result to a name
            # left the name shapeless and its repr fell back to the dict-items
            # assumption -- a SEGFAULT for any pair that isn't (str, int).
            # ir_lower's own repr dispatch already reads both; this is the
            # sema-side half that was missing.
            return (
                list(getattr(e, "el_tuple_types", []) or [])
                or list(getattr(e, "tuple_elem_types", []) or [])
            )
        if isinstance(e, A.Attr):
            return list(getattr(e, "el_tuple_types", []))
        if isinstance(e, A.BinOp):
            return list(getattr(e, "el_tuple_types", []))
        if isinstance(e, A.Subscript):
            # Both spellings, same reason as the A.Call branch: a SLICE of a
            # list[tuple] is itself a list of tuples and carries
            # `el_tuple_types`, while an ELEMENT read carries its own tuple's
            # slots in `tuple_elem_types`. Reading only the latter dropped the
            # shape on `x = ps[:2]`, leaving `x` shapeless -- and a shapeless
            # list[tuple] segfaults in repr.
            return (
                list(getattr(e, "el_tuple_types", []) or [])
                or list(getattr(e, "tuple_elem_types", []) or [])
            )
        return []

    def _inparam_el_type(self, e, scope: Scope) -> str:
        """Element kind of an inparam[T]-valued expression. 'int' if unknown.

        Same "always a bare parameter reference" shape as
        _outparam_el_type -- see its docstring.
        """
        if isinstance(e, A.Name):
            return scope.inparam_el_types.get(e.name, "int")
        return "int"

    def _outparam_el_type(self, e, scope: Scope) -> str:
        """Pointee kind of an outparam[T]-valued expression. 'int' if unknown.

        Unlike list/dict, an outparam value can only ever be a bare
        parameter reference (there's no literal/comprehension/call syntax
        that produces one) -- it's exclusively an exported function's own
        declared parameter.
        """
        if isinstance(e, A.Name):
            return scope.outparam_el_types.get(e.name, "int")
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
        if isinstance(e, A.BinOp):
            return getattr(e, "list_el_type", "int")
        if isinstance(e, A.IfExp):
            # A conditional whose arms are lists: sema stamped the element kind.
            return getattr(e, "list_el_type", "int")
        return "int"

    def _list_el_type_if_known(self, e, scope: Scope) -> str:
        """Like _list_el_type, but returns "any" (not "int") for shapes
        that carry no real, independently-known element-type signal.

        A.MethodCall and A.Call are trusted:
        - MethodCall: builtin string/container methods with a fixed
          return shape (str.split() -> list[str], str.splitlines() ->
          list[str], dict.keys() -> list[str], ...) stamp `list_el_type`
          as a hardcoded literal constant in their own sema dispatch
          (e.g. _check_str_method: `e.list_el_type = "str"`), independent
          of any other inference.
        - Call to a user function: `e.list_el_type` is stamped directly
          from that function's own declared `-> list[T]` return
          annotation (see _check_call: `if ty == "list" and el is not
          None: e.list_el_type = el`) -- a bare `-> list` resolves `el`
          to the "any" sentinel itself (never silently wrong), so this
          is just as direct/reliable as MethodCall's literal stamps, not
          a transitive call back into _list_el_type.

        Deliberately NOT trusting Subscript/Attr/IfExp: those nodes'
        `list_el_type` is frequently stamped by *transitively* calling
        back into _list_el_type on some other sub-expression (e.g. a
        Subscript on a bare-`list`-typed instance field whose element
        type itself defaulted to the "int" placeholder from an empty `[]`
        literal seen elsewhere -- confirmed in practice via
        configparser.py's `self._section_keys.append([]); ...;
        keys: list = self._section_keys[idx]` pattern, which regressed
        when this trusted Subscript). Without auditing every one of the
        ~20 stamping sites in sema.py for whether they're a hardcoded/
        direct literal or a transitive call, only MethodCall and Call
        (both confirmed direct, no recursion into _list_el_type) are
        trusted today.

        Used to decide whether a bare `list` annotation's "any" sentinel
        should be overridden by the RHS's own type, instead of blindly
        trusting _list_el_type's int-or-unknown default (see the call
        site for the bug this guards against: `x: list = []` becoming
        permanently int-only and rejecting later .append()s of any other
        type)."""
        if isinstance(e, A.MethodCall):
            return getattr(e, "list_el_type", "any")
        if isinstance(e, A.Call):
            return getattr(e, "list_el_type", "any")
        return "any"

    def _dict_value_type_if_known(self, e, scope: Scope) -> str:
        """Like _dict_value_type, but returns "any" for shapes with no
        independently-known value-type signal -- see
        _list_el_type_if_known's docstring for the identical reasoning
        (MethodCall's hardcoded-literal stamps and Call's direct
        annotation-derived stamps are both trusted; transitive shapes
        like Subscript/Attr are not)."""
        if isinstance(e, A.MethodCall):
            return getattr(e, "value_type", "any")
        if isinstance(e, A.Call):
            return getattr(e, "value_type", "any")
        return "any"

    def _normalize_instance_type(self, t: str) -> str:
        """A bare known-class name used as a value/element type denotes an
        INSTANCE of that class, so spell it canonically as `instance:<class>`.

        Container value/element kinds derived from an annotation like
        `dict[int, "Proxy"]` or `list[Node]` can surface the class name bare
        (the annotation carries just the name), whereas everywhere else in
        sema an instance type is `instance:<class>`. Left unnormalized, a bare
        name fails the `instance:`-prefix checks that gate list/dict element
        storage and type comparisons, even though it is a perfectly valid
        instance kind. Normalizing here (a single choke point every
        container-value-type lookup passes through) keeps that one spelling
        consistent without special-casing each consumer. Scalars, containers,
        `any`, and already-`instance:`-prefixed strings pass through."""
        if (
            t
            and not t.startswith("instance:")
            and ":" not in t
            and t in self.classes
        ):
            return f"instance:{t}"
        return t

    def _dict_value_type(self, e, scope: Scope) -> str:
        """Value type of a dict-valued expression. 'int' if unknown."""
        return self._normalize_instance_type(self._dict_value_type_inner(e, scope))

    def _dict_value_type_inner(self, e, scope: Scope) -> str:
        if isinstance(e, A.DictLit):
            return getattr(e, "value_type", "int")
        if isinstance(e, A.DictComprehension):
            return getattr(e, "value_type", "int")
        if isinstance(e, A.Name):
            return scope.dict_value_types.get(e.name, "int")
        if isinstance(e, A.Attr):
            return getattr(e, "value_type", "int")
        if isinstance(e, A.Call):
            # A function / method annotated `-> dict[.., V]` stamps the value
            # kind onto the call node (sema fills it from the callee's sig).
            return getattr(e, "value_type", "int")
        if isinstance(e, A.MethodCall):
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
        if isinstance(e, A.Attr):
            return getattr(e, "inner_value_type", "int")
        if isinstance(e, A.Call):
            return getattr(e, "inner_value_type", "int")
        if isinstance(e, A.MethodCall):
            return getattr(e, "inner_value_type", "int")
        if isinstance(e, A.Subscript):
            return getattr(e, "value_type", "int")
        return "int"

    def _dict_value_tuple_types(self, e, scope: Scope) -> list[str]:
        """Per-slot element kinds of a dict's values, when the value kind is
        itself a tuple (e.g. `dict[str, tuple[str, str]]` -> ["str", "str"]).
        [] if unknown / not a tuple-valued dict."""
        if isinstance(e, A.DictLit):
            return list(getattr(e, "value_tuple_elem_types", []))
        if isinstance(e, A.Name):
            return list(scope.dict_value_tuple_types.get(e.name, []))
        if isinstance(e, A.Attr):
            return list(getattr(e, "value_tuple_elem_types", []))
        if isinstance(e, A.Call):
            return list(getattr(e, "value_tuple_elem_types", []))
        if isinstance(e, A.MethodCall):
            return list(getattr(e, "value_tuple_elem_types", []))
        if isinstance(e, A.Subscript):
            return list(getattr(e, "tuple_elem_types", []))
        return []

    def _common_container_inner(self, values: list, scope: Scope) -> str:
        """Common inner value/element kind across a list of nested containers
        (the values of a dict whose value kind is 'dict' or 'list'). Returns
        the shared kind, or 'any' if they disagree, or 'int' if none is known.
        Used to type a chained `outer[k][k2]` read one level deep."""
        seen: str | None = None
        for v in values:
            vt: str = A.expr_type(v)
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
        if isinstance(e, A.Call):
            _e_call: A.Call = e
            return list(_e_call.tuple_elem_types)
        if isinstance(e, A.Subscript):
            _e_sub: A.Subscript = e
            return list(_e_sub.tuple_elem_types)
        if isinstance(e, A.Attr):
            _e_attr: A.Attr = e
            return list(_e_attr.tuple_elem_types)
        if isinstance(e, A.MethodCall):
            _e_mc: A.MethodCall = e
            return list(_e_mc.tuple_elem_types)
        return []

    def _scan_tuple_return(self, stmts: list) -> Optional[list[str]]:
        """Infer the per-slot kinds of a function's tuple return (`return a, b`),
        or None if it never returns a tuple.

        All `return <tuple>` sites of the dominant arity are merged: a slot that
        every return agrees on keeps that kind, a slot they disagree on becomes
        "any". This keeps unpack arity stable while not over-committing the slot
        type for functions with heterogeneous returns (e.g. `_resolve_annot`,
        whose slots are sometimes a name and sometimes None)."""
        # Local annotated variables (`name: list = []`) declared anywhere in
        # this function body, so a `return (a, local_var, b)` slot that's a
        # bare Name referencing one of these can resolve its real kind
        # instead of falling back to the parser's uninitialized default
        # (see _scan_slot_kind's docstring for why that default is unsafe
        # here). Deliberately NOT scope-aware (doesn't track which branch a
        # declaration is reachable from) -- a flat "last annotation wins"
        # table is enough for this best-effort scan; genuine shadowing
        # across branches is rare enough not to special-case.
        local_annots: dict = {}
        self._collect_local_annots(stmts, local_annots)
        shapes: list = []
        self._collect_tuple_returns(stmts, shapes, local_annots)
        if not shapes:
            return None
        # Explicit `: list` intermediate and `: int` arity annotation: shapes[0]
        # is typed "any" (list element with no el_type) so len(shapes[0]) would
        # call _emit_strlen on the list header, reading the capacity field as a
        # C string length (always 1 for small lists) instead of the real arity.
        # Likewise `len(sh)` in the filter loop needs `_sh: list = sh` so
        # `len(_sh)` uses the list-length path.  Without these casts every
        # function that returns a 3-tuple (e.g. `_split_fstring_spec`) appeared
        # to return a 1-tuple, causing spurious "cannot unpack 1-tuple into 3
        # target(s)" errors at every unpack call site.
        _first_shape: list = shapes[0]
        arity: int = len(_first_shape)
        same: list = []
        for sh in shapes:
            _sh: list = sh
            if len(_sh) == arity:
                same.append(_sh)
        merged: list = []
        for i in range(arity):
            # Distinct kinds per slot as a dedup list (not a set + .pop(): a
            # genexpr-in-set and arbitrary set.pop are outside the compilable
            # subset — same idiom as the tuple-membership check).
            kinds: list[str] = []
            for sh in same:
                if sh[i] not in kinds:
                    kinds.append(sh[i])
            merged.append(kinds[0] if len(kinds) == 1 else "any")
        return merged

    def _collect_local_annots(self, stmts: list, acc: dict) -> None:
        """Flat `name -> base kind` table of every annotated local
        assignment (`name: list = []`) reachable in `stmts`, for
        `_collect_tuple_returns`/`_scan_slot_kind` to resolve a bare-Name
        return slot against. Not scope-aware (a later declaration with the
        same name anywhere in the function overwrites an earlier one,
        regardless of which branch either is reachable from) -- fine for
        this best-effort scan; see `_scan_tuple_return`'s call site for
        why genuine per-branch shadowing isn't worth tracking here."""
        for s in stmts:
            if isinstance(s, A.Assign) and s.annot is not None:
                acc[s.target] = s.annot[0]
            elif isinstance(s, A.If):
                s_then: list = s.then
                s_orelse: list = s.orelse
                self._collect_local_annots(s_then, acc)
                self._collect_local_annots(s_orelse, acc)
            elif isinstance(s, A.While):
                s_body: list = s.body
                self._collect_local_annots(s_body, acc)
            elif isinstance(s, A.For):
                s_body: list = s.body
                self._collect_local_annots(s_body, acc)
            elif isinstance(s, A.Try):
                st_body: list = s.body
                st_handler: list = s.handler
                self._collect_local_annots(st_body, acc)
                self._collect_local_annots(st_handler, acc)

    def _scan_slot_kind(self, el, local_annots: dict) -> str:
        """Best-effort static kind of a tuple-return slot expression, for
        use by `_collect_tuple_returns` -- which runs on a RAW, not-yet-
        type-checked function body (that's the whole point: inferring a
        tuple return's shape has to happen before the body is normally
        analyzed, since callers may need it first). `A.expr_type` alone
        isn't safe here: for node kinds sema itself stamps during normal
        checking (`Call`/`MethodCall`/`Name`/etc.), reading it now just
        returns the parser's placeholder default (`Call.inferred_type`
        defaults to "int", so `list(x)` -- a real list-returning builtin
        call -- would otherwise be scanned as "int", silently corrupting
        every caller that unpacks this slot). Recognize the builtin
        container constructors and locally-annotated variables directly;
        anything else still falls back to `A.expr_type`, which is safe for
        literals/already-typed nodes."""
        if isinstance(el, A.ListLit):
            return "list"
        if isinstance(el, A.Call) and el.func in ("list", "sorted"):
            return "list"
        if isinstance(el, A.Call) and el.func == "tuple":
            return "tuple"
        if isinstance(el, A.Call) and el.func in ("dict",):
            return "dict"
        if isinstance(el, A.Call) and el.func in ("set",):
            return "set"
        if isinstance(el, A.Name) and el.name in local_annots:
            return local_annots[el.name]
        # A bare class-name reference in a tuple return (`return (Server,
        # Shared, Client)`) is a first-class "type" object -- the same kind
        # _check_expr stamps for a class name used as a value. This scan runs
        # before the body is type-checked, so the Name's own inferred_type is
        # still the "int" placeholder; recognizing the class name here keeps
        # the slot from collapsing to "int" and lets callers that iterate the
        # returned tuple dispatch classmethods on each element. `self.classes`
        # isn't populated until after this pre-scan (its per-class sigs are
        # built later), so match the raw class definitions in `self.mod.classes`
        # -- those are available now.
        if isinstance(el, A.Name) and (
            any(c.name == el.name for c in self.mod.classes)
            or el.name in BUILTIN_EXCEPTIONS
            or el.name in BUILTIN_TYPE_NAMES
        ):
            return "type"
        return A.expr_type(el)

    def _collect_tuple_returns(self, stmts: list, acc: list, local_annots: dict) -> None:
        # Same explicit `: list` intermediates as _collect_returns, and for
        # the same reason: s.then/s.orelse/s.body/s.handler are opaque
        # attribute reads that must not be passed directly into a recursive
        # call expecting a real `list`.
        for s in stmts:
            if isinstance(s, A.Return) and isinstance(s.value, A.TupleLit):
                slots: list[str] = []
                for el in s.value.elems:
                    if isinstance(el, A.Name) and el.name in self._tuple_scan_globals.types:
                        slots.append(self._tuple_scan_globals.types[el.name])
                    else:
                        slots.append(self._scan_slot_kind(el, local_annots))
                acc.append(slots)
            elif isinstance(s, A.If):
                s_then: list = s.then
                s_orelse: list = s.orelse
                self._collect_tuple_returns(s_then, acc, local_annots)
                self._collect_tuple_returns(s_orelse, acc, local_annots)
            elif isinstance(s, A.While):
                s_body: list = s.body
                self._collect_tuple_returns(s_body, acc, local_annots)
            elif isinstance(s, A.For):
                s_body: list = s.body
                self._collect_tuple_returns(s_body, acc, local_annots)
            elif isinstance(s, A.Try):
                st_body: list = s.body
                st_handler: list = s.handler
                self._collect_tuple_returns(st_body, acc, local_annots)
                self._collect_tuple_returns(st_handler, acc, local_annots)

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
        # Each branch reads its sub-block into an explicit `: list`
        # intermediate before recursing: s.then/s.orelse/s.body/etc. are
        # direct attribute reads on an external/opaque AST-node type (s is
        # generically typed inside this `for s in stmts` loop), so passing
        # them straight into a recursive call that expects `stmts: list`
        # carried the same opacity through and crashed with a null-pointer
        # dereference the moment a function's body had any control flow at
        # all (or, for the top-level call, just by being passed in as
        # fn.body from a caller that never gave it an explicit list type).
        for s in stmts:
            if isinstance(s, A.Return):
                acc.append(s)
            elif isinstance(s, A.If):
                s_then: list = s.then
                s_orelse: list = s.orelse
                self._collect_returns(s_then, acc)
                self._collect_returns(s_orelse, acc)
            elif isinstance(s, A.While):
                s_body: list = s.body
                self._collect_returns(s_body, acc)
            elif isinstance(s, A.For):
                s_body: list = s.body
                self._collect_returns(s_body, acc)
            elif isinstance(s, A.Try):
                st_body: list = s.body
                st_handler: list = s.handler
                self._collect_returns(st_body, acc)
                self._collect_returns(st_handler, acc)
                st_extra: list = s.extra_handlers
                for _types, _bind, hbody in st_extra:
                    self._collect_returns(hbody, acc)
                st_else: list = s.else_body
                st_finally: list = s.finally_body
                self._collect_returns(st_else, acc)
                self._collect_returns(st_finally, acc)

    def _coerce_args_to_param_types(self, e, sig, scope: Scope, skip: int = 0) -> None:
        """Drain an iterable argument that lands on a `list`-annotated
        PARAMETER, so a callee written against a list accepts the other
        iterables CPython would.

        `Counter('mississippi')` is the shape that motivated this: the
        constructor declares `iterable: list[str]`, receives a str, and its
        `for item in iterable` read a raw char pointer as a list header and
        faulted. Same coercion as the sequence builtins get, applied to a
        user-declared parameter instead of a builtin's argument position, so
        every function and constructor in the program (and in the bundled
        stdlib) gets it from one place.

        `skip` drops the leading `self`/`cls` slots the call site does not
        supply.
        """
        _ptypes: list = list(getattr(sig, "param_types", []) or [])
        if not _ptypes:
            return
        for _i in range(len(e.args)):
            _pi = _i + skip
            if _pi >= len(_ptypes) or _ptypes[_pi] != "list":
                continue
            _a = e.args[_i]
            if isinstance(_a, A.Call) and _a.func == "list":
                continue
            if isinstance(_a, (A.Name, A.Attr, A.Subscript)):
                self._check_expr(_a, scope)
            _at = A.expr_type(_a)
            if _at in ("str", "dict", "set"):
                _drained = A.Call(func="list", args=[_a], kwargs=[], pos=_a.pos)
                self._check_expr(_drained, scope)
                e.args[_i] = _drained
            elif _at.startswith("instance:"):
                _cls = _at.split(":", 1)[1]
                if (
                    self._resolve_method(_cls, "__next__") is not None
                    or self._resolve_method(_cls, "__getitem__") is not None
                ):
                    _drained = A.Call(func="list", args=[_a], kwargs=[], pos=_a.pos)
                    self._check_expr(_drained, scope)
                    e.args[_i] = _drained

    def _resolve_param_types(self, f) -> list:
        """`overload` extension: resolve each parameter's static type from
        its annotation, `"any"` when unannotated (dispatch simply treats
        an unannotated param as matching anything -- no attempt to reuse
        the default/usage-hint inference machinery here, since that's
        keyed to a single concrete function body, not a group of
        candidate signatures sharing a name)."""
        param_types: list = []
        f_param_types: list = getattr(f, "param_types", []) or []
        for i in range(len(f.params)):
            annot = f_param_types[i] if i < len(f_param_types) else None
            resolved = self._resolve_annot(annot)
            param_types.append(resolved[0] if resolved is not None else "any")
        return param_types

    def _check_overload_group_distinct(self, name: str, sigs: list, pos) -> None:
        """`overload` extension: reject a group of @overload signatures
        that are indistinguishable from each other (same arity AND same
        param_types) -- dispatch could never pick between them."""
        seen: set = set()
        for sig in sigs:
            key = (sig.arity, tuple(sig.param_types))
            if key in seen:
                raise SemaError(
                    f"two @overload signatures for {name!r} are "
                    f"indistinguishable (same parameter count and types)",
                    pos,
                    ErrorCode.E_OVERLOAD_INCOMPATIBLE,
                )
            seen.add(key)

    def _resolve_overload(self, name: str, sigs: list, args: list, pos, implicit_self: bool = False):
        """`overload` extension: pick the best-matching FuncSig from `sigs`
        for a call site's `args`. Filters by arity match first (accounting
        for defaults, same as ordinary single-signature arity checking),
        then scores the arity-matching candidates by how many parameters
        have an EXACT static-type match against the call site's argument
        types (an unannotated "any" parameter matches anything but scores
        lower than a real match) -- the highest-scoring candidate wins;
        a tie or zero arity-matching candidates is an error. Deliberately
        simpler than full overload resolution (no covariance/promotion
        rules) -- a documented v1 simplification, not every C++/Java
        overload-resolution edge case.

        `implicit_self`: True for a method-form overload call, where
        `sig.arity`/`sig.param_types` include the `self` receiver
        (index 0) but the call-site `args` never do -- every comparison
        below is offset by 1 to account for that.
        """
        offset = 1 if implicit_self else 0
        n = len(args)
        arity_matches = [
            s for s in sigs
            if (s.arity - offset - s.n_defaults) <= n <= (s.arity - offset)
        ]
        if not arity_matches:
            raise SemaError(
                f"no @overload signature for {name!r} accepts {n} "
                f"argument(s)",
                pos,
                ErrorCode.E_OVERLOAD_NO_MATCH,
            )
        arg_types = [A.expr_type(a) for a in args]
        scored: list = []
        for s in arity_matches:
            score = 0
            for i, at in enumerate(arg_types):
                pt_idx = i + offset
                pt = s.param_types[pt_idx] if pt_idx < len(s.param_types) else "any"
                if pt == at:
                    score += 2
                elif pt == "any":
                    score += 1
            scored.append((score, s))
        best = max(sc for sc, _ in scored)
        winners = [s for sc, s in scored if sc == best]
        if len(winners) > 1:
            raise SemaError(
                f"call to {name!r} is ambiguous: matches {len(winners)} "
                f"@overload signatures equally well",
                pos,
                ErrorCode.E_OVERLOAD_AMBIGUOUS,
            )
        return winners[0]

    def _check_must_use(self, e) -> None:
        """`must_use` extension: a bare `ExprStmt` wrapping a call to a
        `@must_use`-decorated function/method discards its return value --
        raise. Only the literal bare-statement case is in scope (no
        dead-store analysis on a captured-but-unused result) -- an
        assignment or a call nested inside another expression is never
        flagged, matching the roster's own narrow spec."""
        if not self._ext_active("must_use"):
            return
        sig = None
        if isinstance(e, A.Call):
            sig = self.funcs.get(e.func)
        elif isinstance(e, A.MethodCall):
            obj_t = A.expr_type(e.obj)
            if obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                resolved = self._resolve_method(cls_name, e.method)
                if resolved is not None:
                    sig = resolved[1]
        if sig is not None and "must_use" in getattr(sig, "decorators", []):
            raise SemaError(
                f"the return value of {getattr(e, 'func', None) or getattr(e, 'method', '?')}"
                f"(...) is discarded, but it is marked @must_use",
                getattr(e, "pos", None),
                ErrorCode.E_MUST_USE_DISCARDED,
            )

    def _check_no_shadowing_global(self, name: str, pos) -> None:
        """`no_shadowing` extension, case (a): a function param/first-local
        sharing a name with a real module-level global
        (`self.global_scope.types`). See docs/EXTENSIONS.md for why this
        compiler only implements two narrow shadow checks rather than
        general lexical shadow detection (Scope is flat, no parent chain)."""
        if not self._ext_active("no_shadowing"):
            return
        if name in self.global_scope.types:
            raise SemaError(
                f"parameter/local {name!r} shadows a module-level global "
                f"of the same name",
                pos,
                ErrorCode.E_SHADOWED_GLOBAL,
            )

    def _check_no_shadowing_free_var(self, name: str, pos) -> None:
        """`no_shadowing` extension, case (b): inside a lifted (nested)
        function, a NEW body-local binding (not a parameter -- those are
        handled by `_check_no_shadowing_global`, since a free-var param can
        never literally collide with its own free-var list by construction)
        that shares a name with one of the function's own captured
        `free_vars` -- i.e. the function locally rebinds a name it also
        captured from its enclosing scope."""
        if not self._ext_active("no_shadowing"):
            return
        if not self.in_lifted or self.in_function is None:
            return
        if name in self._current_free_vars:
            raise SemaError(
                f"local {name!r} shadows a variable captured from the "
                f"enclosing scope",
                pos,
                ErrorCode.E_SHADOWED_GLOBAL,
            )

    def _compute_locked_params(self, f, skip_first: bool = False) -> set:
        """Build the per-function-invocation param-lock set for
        `readonly_params`/`const_params`, consulted by `_require_assignable`.

        `readonly_params`: only the names listed in a preceding
        `@readonly(name, ...)` decorator. `const_params`: every parameter
        (implicit lock), unless the function carries `@mutable_params`
        (a whole-function exemption -- no partial/per-param opt-out).
        Both extensions write into the same set, so having both active is
        naturally redundant-but-harmless (const_params' "all params" is
        already a superset of whatever readonly_params names).

        `skip_first`: for methods, excludes the implicit `self`/`cls`
        receiver from const_params' blanket lock (it's never a real
        rebinding target for user code the way an ordinary parameter is)
        -- `@readonly(name, ...)`'s explicit names are never auto-excluded
        this way, since naming `self` there is a clear, deliberate choice.
        """
        locked: set = set()
        if self._ext_active("readonly_params"):
            readonly_names = list(getattr(f, "readonly_params", []) or [])
            for name in readonly_names:
                if name not in f.params:
                    raise SemaError(
                        f"@readonly names {name!r}, which is not a "
                        f"parameter of {f.name}()",
                        f.pos,
                        ErrorCode.E_READONLY_UNKNOWN_PARAM,
                    )
            locked.update(readonly_names)
        has_mutable_params_deco = "mutable_params" in f.decorators
        if has_mutable_params_deco and not self._ext_active("const_params"):
            raise SemaError(
                f"@mutable_params on {f.name} is not supported -- "
                f"asmpython's compiler-extension system was withdrawn "
                f"(see archived/extensions/)",
                f.pos,
                ErrorCode.E_DECORATOR_WITHOUT_EXTENSION,
            )
        if self._ext_active("const_params") and not has_mutable_params_deco:
            params = f.params[1:] if skip_first and f.params else f.params
            locked.update(params)
        return locked

    def _require_assignable(self, name: str, pos) -> None:
        """Raise E_CONST_REASSIGNED if `name` was ever declared `const`.

        This is the single shared check used by every statement form that
        can bind/rebind a name -- plain assignment, augmented assignment,
        `del`, multi-assign, tuple/list destructuring (including starred),
        `for` loop targets, `except ... as` binding, and import aliases --
        so a const name can never be rebound through any of them. Mutating
        an object a const name refers to (e.g. `values.append(3)` after
        `const values = [1, 2]`) is unaffected: this only guards *rebinding
        the name itself*, never method calls/subscript-writes on the value
        it holds.
        """
        if name in self.const_names:
            raise SemaError(
                f"cannot reassign const {name!r} (declared at "
                f"line {self.const_names[name].line})",
                pos,
                ErrorCode.E_CONST_REASSIGNED,
            )
        # `readonly_params`/`const_params` extensions: a per-function-
        # invocation lock set, rebuilt fresh at the start of each function/
        # method body check (see _locked_params' population sites) --
        # unlike const_names (locked forever, module-wide), this only
        # applies for the duration of the ONE function body currently being
        # checked, since it's the same parameter name potentially reused
        # (and freely reassignable) in a different function.
        if name in self._locked_params:
            raise SemaError(
                f"cannot reassign parameter {name!r}: locked by "
                f"'@readonly' or the 'const_params' extension",
                pos,
                ErrorCode.E_READONLY_PARAM_REASSIGNED,
            )
        # `no_global_mutation` extension: a write to a name that is a real
        # module-level global, from inside a function that never declared
        # `global name` for it. Checked against self.global_scope.types --
        # the one authoritative global table -- rather than the function's
        # own flat `Scope`, which (being a one-time dict-copy seeded by
        # _seed_globals_into, not a live reference) can't itself distinguish
        # "this is the module global's copy" from "an ordinary same-named
        # function-local" once seeded.
        if (
            self._ext_active("no_global_mutation")
            and self.in_function is not None
            and name in self.global_scope.types
            and name not in self._globals_declared_in.get(self.in_function, set())
        ):
            raise SemaError(
                f"cannot reassign global {name!r} without a 'global "
                f"{name}' declaration",
                pos,
                ErrorCode.E_UNDECLARED_GLOBAL_MUTATION,
            )

    def _bind_name_from_value(
        self, target: str, value, scope: Scope, annot: tuple | None = None
    ) -> None:
        """Bind `target` in `scope` to the static type of `value`, the same
        way a plain `target = value` assignment would. Shared by `A.Assign`
        and `A.NamedExpr` (the walrus operator `target := value`)."""
        self._require_assignable(target, getattr(value, "pos", None))
        # `no_shadowing` extension: only check on the FIRST bind of `target`
        # within the CURRENTLY-checked function (a later reassignment of an
        # already-declared local isn't shadowing). `self._locally_bound`
        # (not `scope.types`, which is pre-seeded with every module global
        # before any body statement runs and so can't distinguish "first
        # real local bind" from "matches a global's name") is the real
        # first-bind signal. Case (a) (module-global shadow) applies to
        # every function; case (b) (free-var shadow) is a no-op outside a
        # lifted function (checked inside the helper itself).
        if self.in_function is not None and target not in self._locally_bound:
            pos = getattr(value, "pos", None)
            self._check_no_shadowing_global(target, pos)
            self._check_no_shadowing_free_var(target, pos)
            self._locally_bound.add(target)
        # Remember a name bound directly to a lambda, so a later `name(...)`
        # call recovers the lambda's result type instead of defaulting int.
        if isinstance(value, A.Lambda):
            self.lambda_rets[target] = getattr(value, "lambda_ret", "int")
            self._infer_lambda_param_types(target, value)
        t: str = A.expr_type(value)
        # `no_implicit_any` extension: an unannotated assignment whose value
        # is genuinely opaque (the real "any" marker A.expr_type returns for
        # specific unresolvable-inference cases -- NOT the much more common
        # "int"-as-unknown-sentinel default, which is load-bearing internal
        # inference fallback throughout this compiler and would false-
        # positive constantly if included here). An explicit `target: T =
        # value` annotation always overrides this, checked below.
        if self._ext_active("no_implicit_any") and annot is None and t == "any":
            raise SemaError(
                f"assignment to {target!r} has no inferrable concrete type",
                getattr(value, "pos", None),
                ErrorCode.E_IMPLICIT_ANY_ASSIGN,
            )
        if (
            t == "int"
            and isinstance(value, A.Call)
            and value.func[:1].isupper()
            and value.func not in self.funcs
            and value.func not in self.classes
        ):
            scope.add(target, "any")
            return
        # A declaration annotation (`name: T = value`) overrides inference
        # when it constrains the type — this is how `xs: list[str] = []`
        # pins the element kind even though the empty initializer infers
        # nothing. Honor it only when the inferred type is the unknown
        # default ("int") or the annotation refines a same-kind container.
        ann = self._resolve_annot(annot)
        if ann is not None:
            # Same fix as _seed_param/_collect_field_types: subscript reads
            # with an explicit `aty: str`, not a tuple-unpack, so `aty ==
            # "list"` / `aty == "dict"` below route through _runtime_str_eq.
            # This is the path `name: dict = {}`-style annotated local
            # assignments go through (_check_stmt's A.Assign -> here), so it
            # was the actual remaining cause of bug #9's core symptom: a
            # `: dict`-annotated local never got recognized as dict-typed in
            # the selfhosted binary, so `d[k] = v` miscompiled as a raw
            # indexed write instead of _runtime_dict_set.
            aty: str = ann[0]
            ael = ann[1]
            aval = ann[2]
            atup: list = ann[3]
            aelval = ann[4]
            # An explicit `object`/`Any` annotation (aty == "any") is
            # ALWAYS authoritative, even when the initializer has a concrete
            # type: `kk: object = "answer"` is deliberately heterogeneous, so
            # `kk` must stay "any" (not narrow to "str"). Otherwise a later
            # `show(kk)` into an `object` parameter re-boxes the already-boxed
            # value -- a double box whose payload is the inner box pointer, so
            # unboxing once yields garbage. Without the `aty == "any"` term the
            # condition below (`t in ("int","any") or t == aty`) is False for a
            # str/list/... initializer and the explicit `object` annotation is
            # silently dropped.
            if t in ("int", "any") or t == aty or aty == "any":
                if aty == "list":
                    # A bare `list` annotation (no `list[T]` parameter)
                    # resolves its element type to the "any" sentinel
                    # (_resolve_scalar_annot's deliberate "unconstrained"
                    # marker) -- but "any" is truthy, so a naive `ael or
                    # ...` always picks it over the value's own better-
                    # known element type, discarding real type info (e.g.
                    # `x: list = s.split(...)` is really list[str], but
                    # `ael` alone can't see that). Treat "any" as "no
                    # annotation-derived info" so the value's inferred
                    # element type can win instead -- but only when the
                    # RHS shape actually carries genuine inferred
                    # information (MethodCall/Call/Subscript/Attr/IfExp,
                    # which sema stamps `list_el_type` onto when known).
                    # An empty `[]` ListLit carries none -- its el_type
                    # defaults to "int" as a placeholder, not a real
                    # signal -- so `x: list = []` (declare-empty-then-
                    # append-anything, a very common pattern) must keep
                    # resolving to "any"/lenient, or every later .append()
                    # of a non-int value would wrongly fail to type-check.
                    el_type = ael if (ael and ael != "any") else self._list_el_type_if_known(value, scope)
                    # An EXPLICIT `list[object]` (raw annot ('list','any'), vs a
                    # bare `list` whose raw annot is ('list', None)) is a
                    # genuinely heterogeneous list: track it so ir_lower boxes
                    # scalars appended into it and its element reads stay "any".
                    # A bare `list` (element kind simply unknown) is left alone,
                    # so existing homogeneous-list code keeps raw elements.
                    if annot == ("list", "any"):
                        el_type = "any"
                        self._explicit_object_lists.add(target)
                    else:
                        self._explicit_object_lists.discard(target)
                    scope.add(
                        target,
                        "list",
                        el_type=el_type,
                        el_value_type=aelval,
                        el_tuple_types=atup or None,
                    )
                    return
                if aty == "dict":
                    # Same "any" truthiness trap as the list branch above.
                    value_type = aval if (aval and aval != "any") else self._dict_value_type_if_known(value, scope)
                    # An EXPLICIT `dict[str, object]` (aval == "any") is a
                    # genuinely heterogeneous dict: keep its value kind "any"
                    # and never let a later single-kind write narrow it (see
                    # `_explicit_object_dicts`), so its scalar values are boxed
                    # in / unboxed out and `type(d[k])` stays answerable. A
                    # bare `dict`/`{}` (annot None, never reaches this branch)
                    # keeps its "first write teaches the kind" narrowing.
                    if aval == "any":
                        value_type = "any"
                        self._explicit_object_dicts.add(target)
                        # Flag the RHS literal so ir_lower boxes each scalar
                        # value at construction (an EXPLICIT
                        # `{...}: dict[str, object]`). Gated by this flag, NOT
                        # value_type=="any", so a bare `{...}` whose values are
                        # merely mixed (also value_type "any") is NOT boxed --
                        # those are consumed raw by bare-`dict` readers that
                        # never unbox, and boxing would break a `d[k] == "x"`
                        # compare (confirmed: 424_match_structural). Only this
                        # annotated-object-dict case has an "any"-typed reader
                        # on the variable that will unbox.
                        if isinstance(value, A.DictLit):
                            value.value_type = "any"
                            value.box_values = True
                    else:
                        self._explicit_object_dicts.discard(target)
                    scope.add(
                        target,
                        "dict",
                        value_type=value_type,
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
                if aty in ("str", "float", "int", "any") or aty.startswith("instance:"):
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
            # A name re-assigned to a sibling class instance across `if`/`elif`
            # branches that share scope (`gen = WindowsCodegen(...)` in one
            # branch, `gen = Freestanding16Codegen(...)` in another) must widen
            # to their common ancestor rather than let the last-checked branch
            # win outright — code after the merge point calls methods on `gen`
            # expecting virtual dispatch across all the branches' classes, not
            # just whichever one happened to be assigned last in source order.
            if t.startswith("instance:") and target in scope.types:
                prev_t = scope.types[target]
                if prev_t.startswith("instance:") and prev_t != t:
                    prev_cls, new_cls = prev_t[len("instance:"):], t[len("instance:"):]
                    common = self._common_class_ancestor(prev_cls, new_cls)
                    if common is not None:
                        t = f"instance:{common}"
            scope.add(
                target,
                t,
                is_bool=t == "int" and A.is_bool_expr(value),
                is_none=t == "int" and A.is_none_expr(value),
            )

    def _check_stmt(self, s, scope: Scope) -> "Optional[list]":
        if isinstance(s, A.Pass):
            return
        if isinstance(s, A.YieldStmt):
            self._check_expr(s.value, scope)
            return
        if isinstance(s, A.ClosureBind):
            # A nested `def` that captures outer variables.
            # Validate that each free variable is in scope.
            for fv in s.free_vars:
                if fv not in scope.types and fv not in self.global_scope.types:
                    pass  # Accept unknown names; may be a global defined later.
            # Type is "closure" so codegen can distinguish from plain lists.
            scope.add(s.func_name, "closure")
            return
        if isinstance(s, A.Assign):
            self._check_expr(s.value, scope)
            # Direct field access, not getattr(s, "annot", None): s is
            # confirmed isinstance(s, A.Assign) here, and annot is always a
            # real field on it. A getattr() result is opaque ("any"-typed)
            # to sema, and that opacity propagates into _bind_name_from_value's
            # own `annot=None` parameter (itself unannotated, so it silently
            # defaults to "int" instead of carrying the real (base, el)
            # tuple shape) -- breaking the explicit `: T = value` annotation
            # override for every plain `A.Assign` in the entire compiler,
            # including the `func_name: str = e.func` pattern used to fix
            # the opaque-attribute bug class everywhere else this session.
            self._bind_name_from_value(s.target, s.value, scope, s.annot)
            return
        if isinstance(s, A.ConstDecl):
            # Reuses _bind_name_from_value's own const-lock check (harmless
            # to call twice for a genuinely fresh const name: the name isn't
            # in self.const_names yet at this point, so it's a no-op here
            # and only becomes load-bearing if this exact name is declared
            # `const` a second time).
            self._require_assignable(s.name, s.pos)
            if s.name in self.funcs or s.name in self.classes:
                # Only catchable in this direction: def/class names are
                # collected in a pre-pass that runs before module-body
                # statements (including this ConstDecl) are walked at all,
                # so `self.funcs`/`self.classes` are fully populated here
                # regardless of source order. The reverse order (`const foo
                # = 1` followed later by `def foo(): ...`) can't be caught
                # the same way -- and real CPython doesn't reject that
                # shape either (the later def just clobbers the earlier
                # binding) -- so this asymmetry is deliberate, not a bug.
                # See docs/EXTENSIONS.md.
                raise SemaError(
                    f"cannot declare const {s.name!r}: a function or class "
                    f"with that name already exists",
                    s.pos,
                    ErrorCode.E_CONST_REDEFINED,
                )
            self._check_expr(s.value, scope)
            self._bind_name_from_value(s.name, s.value, scope, s.annotation)
            self.const_names[s.name] = s.pos
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
                        ErrorCode.E_STARRED_ASSIGN_NOT_LIST,
                    )
                rhs_t: str = A.expr_type(s.values[0])
                if rhs_t != "list":
                    raise SemaError(
                        f"starred assignment requires a list on the "
                        f"right-hand side, got {rhs_t}",
                        s.pos,
                        ErrorCode.E_STARRED_ASSIGN_NOT_LIST,
                    )
                el = self._list_el_type(s.values[0], scope)
                el_bound = el if el != "int" else "any"
                for t in s.targets:
                    self._require_assignable(t.name, s.pos)
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
                    ErrorCode.E_TUPLE_ASSIGN_MIXED_TARGETS,
                )
            # Unpack form: `a, b = <single tuple expr>` (a literal, a tuple
            # variable, or a call to a tuple-returning function).
            if len(s.values) == 1 and A.expr_type(s.values[0]) == "tuple":
                ets = self._tuple_elem_types(s.values[0], scope)
                if ets and len(ets) != len(s.targets):
                    raise SemaError(
                        f"cannot unpack {len(ets)}-tuple into {len(s.targets)} target(s)",
                        s.pos,
                        ErrorCode.E_UNPACK_COUNT,
                    )
                # Bind each target from the tuple's per-slot kind. A missing
                # slot, or an "int" slot (asmpython's unknown sentinel — a slot
                # holding an inferred-but-untracked object, e.g. a FuncSig
                # pulled from a dict), binds opaque so `target.attr` stays
                # lenient. A concrete scalar/instance slot keeps its kind.
                direct_tuple_shape = isinstance(
                    s.values[0], (A.TupleLit, A.Call, A.MethodCall)
                )
                for i, t in enumerate(s.targets):
                    self._require_assignable(t.name, s.pos)
                    slot = ets[i] if i < len(ets) else "any"
                    scope.add(
                        t.name,
                        slot if (direct_tuple_shape or slot != "int") else "any",
                    )
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
                    known_el = self._list_el_type_if_known(s.values[0], scope)
                    if known_el != "any":
                        el = known_el
                    else:
                        el = self._list_el_type(s.values[0], scope)
                        el = el if el != "int" else "any"
                elif A.expr_type(s.values[0]) == "str":
                    el = "str"
                for t in s.targets:
                    self._require_assignable(t.name, s.pos)
                    scope.add(t.name, el)
                return
            # Parallel form: `a, b = e1, e2`.
            if len(s.targets) != len(s.values):
                raise SemaError(
                    f"tuple assign expects {len(s.targets)} values, got {len(s.values)}",
                    s.pos,
                    ErrorCode.E_UNPACK_COUNT,
                )
            for t, v in zip(s.targets, s.values):
                vt: str = A.expr_type(v)
                # `float` is fine on the x86-64 IR backend: each RHS is lowered
                # by `_lower_expr` (an f64 value for a float) and stored via
                # `_store_tuple_assign_target`, which slots it at its own
                # `val.type` -- no "everything through rax" assumption (that was
                # the legacy NASM backend, which this path no longer targets).
                # So `a, b = 1.5, 2.5` and `x, y = y, x` on floats work.
                if vt not in (
                    "int",
                    "str",
                    "any",
                    "list",
                    "dict",
                    "tuple",
                    "set",
                    "float",
                ) and not vt.startswith("instance:"):
                    raise SemaError(
                        f"tuple assign target: unsupported value type {vt}",
                        s.pos,
                        ErrorCode.E_TUPLE_ASSIGN_VALUE_TYPE,
                    )
                self._check_tuple_assign_target(t, vt, scope, s.pos)
            return
        if isinstance(s, A.AugAssign):
            self._require_assignable(s.target, s.pos)
            if s.target not in scope.types:
                raise SemaError(
                    f"augmented assignment to undefined variable {s.target!r}",
                    s.pos,
                    ErrorCode.E_UNDEFINED_NAME,
                )
            self._check_expr(s.value, scope)
            # `d |= other` (PEP 584): in-place dict union, merging `other`'s
            # entries into `d` (overwriting on key conflicts). `s |= other`:
            # in-place set union, identical codegen since sets are
            # dict-backed (codegen.py's AugAssign handler) — previously
            # only "dict" was checked here, so `some_set |= 5` (or any
            # non-set RHS) silently passed sema instead of raising.
            target_ty = scope.types.get(s.target)
            if s.op == "|" and target_ty in ("dict", "set"):
                rt: str = A.expr_type(s.value)
                if rt not in (target_ty, "any"):
                    raise SemaError(
                        f"unsupported operand type for |=: {target_ty} |= {rt}", s.pos,
                        ErrorCode.E_BINARY_OP_TYPE,
                    )
                return
            # `b += 1` etc. demotes a tracked bool back to a plain int, as in
            # CPython (bool has no augmented-assign dunders of its own).
            scope.bool_flags[s.target] = False
            scope.none_flags[s.target] = False
            return
        if isinstance(s, A.Return):
            if self.in_function is None:
                raise SemaError("'return' outside of a function", s.pos, ErrorCode.E_RETURN_OUTSIDE_FUNC)
            if s.value is not None:
                self._check_expr(s.value, scope)
                self._learn_return_element_kind(s.value, scope)
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
            # Guard every possible loop-target name against const-rebinding
            # up front, before any of the zip/enumerate/multi-target/
            # single-var sub-dispatch below runs -- this covers all shapes
            # in one place rather than patching each of their scope.add
            # call sites individually. `s.targets` entries are normally a
            # name (str); nested unpacking (e.g. `for i, (a, b) in ...`)
            # can nest a list[str], so flatten recursively.
            def _flatten_for_targets(names) -> list:
                flat: list = []
                for n in names:
                    if isinstance(n, list):
                        flat.extend(_flatten_for_targets(n))
                    else:
                        flat.append(n)
                return flat

            for _nm in _flatten_for_targets([s.var] if not s.targets else s.targets):
                self._require_assignable(_nm, s.pos)

            # zip(A, B) / enumerate(zip(A, B)): parallel iteration with an
            # optional index. Recognized before the plain-enumerate handler.
            zspec = self._for_zip_spec(s)
            if zspec is not None:
                idx_name, znames, zexprs = zspec
                for ze in zexprs:
                    self._check_expr(ze, scope)
                    if A.expr_type(ze) not in ("list", "tuple", "any"):
                        raise SemaError("zip() arguments must be lists or tuples", s.pos, ErrorCode.E_ZIP_ARGS)
                if idx_name is not None:
                    scope.add(idx_name, "int")
                for zn, ze in zip(znames, zexprs):
                    scope.add(zn, self._iter_element_type(ze, scope))
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
                _enum_start_kwarg = None
                for _kn, _kv in getattr(s.iter, "kwargs", []):
                    if _kn == "start":
                        _enum_start_kwarg = _kv
                _enum_n_args = len(s.iter.args) + (1 if _enum_start_kwarg else 0)
                if _enum_n_args not in (1, 2):
                    raise SemaError(
                        "enumerate() takes 1 or 2 arguments", s.pos,
                        ErrorCode.E_ARG_COUNT,
                    )
                if len(s.targets) != 2:
                    raise SemaError(
                        "for ... in enumerate(...) needs two targets "
                        "(`for i, x in enumerate(xs)`)",
                        s.pos,
                        ErrorCode.E_UNPACK_TARGET_COUNT,
                    )
                inner = s.iter.args[0]
                self._check_expr(inner, scope)
                # `for i, v in enumerate(<iterable object>)`: this for-position
                # handler runs before the generic call check, so apply the same
                # drain-with-list() coercion here (see
                # `_coerce_iterable_instance_args`).
                self._coerce_iterable_instance_args(s.iter, scope)
                inner = s.iter.args[0]
                _start_arg = s.iter.args[1] if len(s.iter.args) == 2 else _enum_start_kwarg
                if _start_arg is not None:
                    self._check_expr(_start_arg, scope)
                    if A.expr_type(_start_arg) != "int":
                        raise SemaError(
                            "enumerate() start argument must be an int",
                            s.pos,
                            ErrorCode.E_ARG_TYPE,
                        )
                scope.add(s.targets[0], "int")
                enum_el_t = self._iter_element_type(inner, scope)
                if enum_el_t == "dict":
                    enum_inner = self._list_el_value_type(inner, scope)
                    scope.add(
                        s.targets[1],
                        "dict",
                        value_type=enum_inner if enum_inner != "int" else "any",
                    )
                elif enum_el_t == "list":
                    enum_inner = self._list_el_value_type(inner, scope)
                    scope.add(
                        s.targets[1],
                        "list",
                        el_type=enum_inner if enum_inner != "int" else "any",
                    )
                else:
                    scope.add(s.targets[1], enum_el_t)
                self.loop_depth += 1
                try:
                    self._check_block(s.body, scope)
                finally:
                    self.loop_depth -= 1
                return
            if s.iter is not None:
                self._check_expr(s.iter, scope)
                it_t: str = A.expr_type(s.iter)
                # Multi-target unpack (`for a, b in <iterable-of-pairs>`): each
                # element is itself a tuple/pair whose per-slot kinds we don't
                # track, so bind every target leniently. Handles list-of-tuples
                # and tuple-of-tuples uniformly. (zip/enumerate were already
                # handled above with precise element kinds.)
                if s.targets and it_t in ("list", "tuple", "dict", "str", "set", "any", "int"):
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
                                ErrorCode.E_UNPACK_NOT_ITERABLE,
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
                        # An "int" slot binds as int rather than falling into
                        # the lenient bucket. A target left "any" is a value
                        # whose static kind is genuinely unknown, and every
                        # consumer that needs a real kind has to guess -- the
                        # dict-key encoder guessed "already a string pointer"
                        # and hashed a raw integer as an address, so
                        # `for k, v in d.items(): inv[v] = k` segfaulted for
                        # any int-valued dict. The slot kind IS known here.
                        is_int = flat and ti < len(shape) and shape[ti] == "int"
                        if is_str:
                            scope.add(nm, "str")
                            ttypes.append("str")
                        elif is_flt:
                            scope.add(nm, "float")
                            ttypes.append("float")
                        elif is_int:
                            scope.add(nm, "int")
                            ttypes.append("int")
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
                    if el_t == "?":
                        el_t = "any"
                    # A `list[Node]` yields INSTANCES of Node; the raw element
                    # kind is the annotation's bare class name. Normalize to
                    # `instance:<class>` so the loop variable's later uses pass
                    # the `instance:`-prefix gates (append/method-call). Leaves
                    # tuple/dict/list/any/scalars untouched -- their dedicated
                    # branches below still match.
                    el_t = self._normalize_instance_type(el_t)
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
                    # Iterating a tuple binds the loop var to a single element
                    # type. A homogeneous tuple uses that type directly; a
                    # heterogeneous one binds "any" (every slot is a uniform
                    # 8-byte value, read opaquely per iteration) -- e.g. a
                    # `(int, str, list)` record iterated generically. Only a
                    # tuple whose kinds mix float with a pointer-sized kind
                    # stays rejected: a float slot lives in its bit pattern and
                    # an "any"/int iteration variable would misread it, so
                    # those must be indexed element-by-element instead.
                    ets = self._tuple_elem_types(s.iter, scope)
                    if not ets:
                        scope.add(s.var, "int")
                    elif _all_same(ets):
                        scope.add(s.var, ets[0])
                    elif "float" not in ets:
                        scope.add(s.var, "any")
                    else:
                        raise SemaError(
                            "cannot iterate a tuple that mixes float with other "
                            "kinds; index its elements instead",
                            s.pos,
                            ErrorCode.E_HETEROGENEOUS_TUPLE,
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
                elif it_t.startswith("instance:"):
                    # User-defined iterable: class must have __iter__ and __next__.
                    cls_name = it_t.split(":", 1)[1]
                    cls_sig: ClassSig = self.classes.get(cls_name)
                    if cls_sig is None:
                        raise SemaError(
                            f"cannot iterate over {cls_name!r}: unknown class",
                            s.pos,
                            ErrorCode.E_UNDEFINED_NAME,
                        )
                    cls_methods = cls_sig.methods
                    if "__iter__" not in cls_methods or "__next__" not in cls_methods:
                        raise SemaError(
                            f"cannot iterate over {cls_name!r}: "
                            f"class must define __iter__ and __next__",
                            s.pos,
                            ErrorCode.E_ITER_TYPE,
                        )
                    # Determine element type from __next__'s declared return type.
                    next_sig = cls_methods.get("__next__")
                    el_t = "any"
                    if next_sig is not None and next_sig.ret_type is not None:
                        el_t = next_sig.ret_type[0]
                    # Mark the For node so codegen uses the iterator protocol path.
                    s.iter_is_instance = cls_name
                    scope.add(s.var, el_t)
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
                        ErrorCode.E_ITER_TYPE,
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
                raise SemaError("'break' outside a loop", s.pos, ErrorCode.E_BREAK_OUTSIDE_LOOP)
            return
        if isinstance(s, A.Continue):
            if self.loop_depth == 0:
                raise SemaError("'continue' outside a loop", s.pos, ErrorCode.E_CONTINUE_OUTSIDE_LOOP)
            return
        if isinstance(s, A.Import):
            # `import a.b.c [as d]`: the name later `x.attr` lookups bind
            # through is the alias if present (`d`), else the dotted path's
            # leading segment ("os.path" -> "os"). Real submodule lookup
            # (whole-program merge resolving s.module, the FULL dotted path,
            # to lumen/audio.py etc.) is post-bootstrap/program.py's job;
            # this only needs to get the right NAME into scope so `audio.x`
            # parses and type-checks instead of falling through to an
            # opaque-int default.
            _im_alias: str = getattr(s, "alias", "") or ""
            _im_module: str = getattr(s, "module", "") or ""
            _im_parts: list = _im_module.split(".")
            top_name: str = _im_parts[0]
            bind_name: str = _im_alias if _im_alias else top_name
            self._require_assignable(bind_name, s.pos)
            try:
                bindings = _load_module(top_name)
            except SemaError:
                # Module isn't in asmpython's stdlib registry — accept the
                # statement as a parser-level no-op so source that uses
                # standard CPython modules can still be checked. The name
                # becomes a dummy in scope; any subsequent `x.attr` lookup
                # will still error at the attribute resolution step.
                scope.add(bind_name, "module")
                return
            self.imported_modules[bind_name] = bindings
            # Make `math` a known name in scope (as a dummy int) so `math.x`
            # parses cleanly past the Name lookup.
            scope.add(bind_name, "module")
            return
        if isinstance(s, A.FromImport):
            # `from asmpython.stdlib import os` (the compiler imports its stdlib
            # by full path to dodge CPython's stdlib at compile time): bind each
            # imported name that names a stdlib module as an FFI module, so
            # `os.fopen(...)` dispatches through BINDINGS just like a bare
            # `import os`. Names that aren't stdlib modules fall through to the
            # generic handling below.
            _fi_module: str = getattr(s, "module", "") or ""
            _fi_level: int = getattr(s, "level", 0) or 0
            _fi_names: list = getattr(s, "names", []) or []
            _fi_orig_names: list = getattr(s, "orig_names", []) or []
            if _fi_level == 0 and (
                _fi_module in ("asmpython.stdlib", "asmpython._stdlib")
                or _fi_module.startswith("asmpython.stdlib.")
                or _fi_module.startswith("asmpython._stdlib.")
            ):
                for name, orig in zip(_fi_names, _fi_orig_names or _fi_names):
                    try:
                        self.imported_modules[name] = _load_module(orig)
                        self._require_assignable(name, s.pos)
                        scope.add(name, "module")
                    except SemaError:
                        # A stdlib *submodule* that isn't an FFI binding set
                        # (e.g. `ospath`). Bind it as a module so `name.func(...)`
                        # dispatches to the merged
                        # project function (whole-program) — unless the name was
                        # already bound (e.g. a materialized value global like
                        # `BINDINGS`): re-binding would clobber its real type.
                        # Uppercase names are constants/classes, not submodules.
                        if name not in scope.types:
                            self._require_assignable(name, s.pos)
                            ty = "any" if orig[:1].isupper() else "module"
                            scope.add(name, ty)
                return
            # `from asmpython.assembly import assembly_func, include`: compiler
            # directives. `assembly_func` is consumed at parse time as a
            # decorator. Bind all names from the package as opaque markers.
            if _fi_module in ("asmpython.assembly", "assembly") and _fi_level == 0:
                for name in _fi_names:
                    self._require_assignable(name, s.pos)
                    scope.add(name, "asmdirective")
                return
            # Relative import or unknown module: accept the syntax and bind
            # each imported name as a dummy int. Self-host needs every source
            # file to *parse*; real cross-file resolution comes later.
            if _fi_level > 0 or not _fi_module:
                # `from . import ast_nodes as A` (no module name, just dots)
                # imports sibling *modules* — bind them as modules so
                # `A.Module(...)` / `A.expr_type(...)` stay lenient. A relative
                # *name* import (`from .x import Y`) binds an opaque value
                # ("any") so `Y(...)` / `Y.method()` / `Y.attr` all stay lenient
                # rather than erroring as operations on an int.
                if not _fi_module:
                    # No module name at all after the dots: either a sibling-
                    # module import (`from . import ast_nodes as A`, always
                    # aliased in this codebase since the bare name would
                    # collide with the file itself being imported elsewhere),
                    # or a bare value pulled from a package's __init__.py
                    # (`from .. import __version__`, never aliased — there's
                    # nothing to alias away from). Use that to tell them apart:
                    # an `as`-aliased name is the module case; an unaliased
                    # name is the value case, whose real type the whole-program
                    # loader's _materialize_value_imports already prepended as
                    # a real `Assign` ahead of this statement, so scope[name]
                    # must not be clobbered back to "module".
                    orig_names: list = _fi_orig_names or _fi_names
                    for name, orig in zip(_fi_names, orig_names):
                        if name != orig:
                            self._require_assignable(name, s.pos)
                            scope.add(name, "module")
                        elif name not in scope.types:
                            self._require_assignable(name, s.pos)
                            scope.add(name, "any")
                    return
                # `from .sibling import orig as local`: a relative import WITH
                # a module name. Whole-program merge has already inlined
                # `sibling`'s top-level functions/classes into this same
                # module (this is the path driver.py's
                # `from .sema import analyze as sema_analyze` takes), so an
                # aliased name here isn't really opaque -- it's a real call
                # target hiding under a local name. Without registering it in
                # func_aliases, `sema_analyze(...)` bound as plain opaque
                # "any" and never resolved to the merged `analyze` function:
                # _check_call found no match in self.funcs or func_aliases,
                # so the whole call silently failed to bind to anything and
                # codegen had no symbol to emit it against -- the entire
                # statement vanished from the compiled output with no error,
                # since this self-host bootstrapping path is intentionally
                # lenient about unresolved relative imports.
                orig_names2: list = _fi_orig_names or _fi_names
                for name, orig in zip(_fi_names, orig_names2):
                    if name != orig:
                        self.mod.func_aliases[name] = orig
                    if name not in scope.types:
                        self._require_assignable(name, s.pos)
                        scope.add(name, "any")
                return
            try:
                bindings = _load_module(_fi_module)
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
                orig_names3: list = _fi_orig_names or _fi_names
                for local, orig in zip(_fi_names, orig_names3):
                    if local != orig:
                        self.mod.func_aliases[local] = orig
                    if local not in scope.types:
                        self._require_assignable(local, s.pos)
                        scope.add(local, "any")
                return
            bindings: dict = bindings
            for name in _fi_names:
                if name not in bindings:
                    # Unknown binding inside a known module — accept as an
                    # opaque value (mirrors the unknown-module fallback above).
                    self._require_assignable(name, s.pos)
                    scope.add(name, "any")
                    continue
                b = bindings[name]
                if isinstance(b, stdlib.Func):
                    self.ffi_funcs[name] = b
                else:
                    self.ffi_consts[name] = b
                    self._require_assignable(name, s.pos)
                    scope.add(name, getattr(b, "ty", "int"))
            return
        if isinstance(s, A.ExprStmt):
            self._check_expr(s.expr, scope)
            self._check_must_use(s.expr)
            return
        if isinstance(s, A.IndexAssign):
            self._check_expr(s.target.obj, scope)
            self._check_expr(s.target.index, scope)
            obj_t: str = A.expr_type(s.target.obj)
            self._check_expr(s.value, scope)
            value_t: str = A.expr_type(s.value)
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
                    and not (el_t.startswith("instance:") and value_t.startswith("instance:"))
                ):
                    raise SemaError(
                        f"list[i] = v: list element type is {el_t}, got {value_t}",
                        s.pos,
                        ErrorCode.E_ASSIGN_TYPE,
                    )
            elif obj_t == "outparam":
                # `out[i] = value`: write-through for an exported function's
                # raw-pointer-out parameter. `i` is usually the literal 0
                # (a single scalar out-parameter, e.g. `size_t *out_size`
                # -- the common case), but a real index expression (a loop
                # counter) is equally valid for the array-out-parameter
                # shape (e.g. `uint8_t *buffer` filled byte-by-byte) --
                # same index flexibility as inparam[T]'s read side, just
                # the write direction.
                if A.expr_type(s.target.index) not in ("int", "any"):
                    raise SemaError(
                        "outparam[T] index must be an int", s.pos, ErrorCode.E_INDEX_TYPE
                    )
                el_t = self._outparam_el_type(s.target.obj, scope)
                # Unlike list/dict elements, outparam[T]'s "int" is a real,
                # explicit, user-declared pointee kind -- not asmpython's
                # usual int-as-unknown-sentinel -- so it's NOT treated as a
                # wildcard here; only "any" (a genuinely unconstrained
                # value, e.g. an unannotated parameter) stays lenient.
                # outparam[int8]'s pointee is a raw C ABI byte width, not a
                # real asmpython value type -- an ordinary int expression
                # (0-255) is what a caller actually writes there, same as
                # inparam[int8]'s read side normalizes to plain "int".
                expected_t = "int" if el_t in ("int8", "int32") else el_t
                if value_t != "any" and expected_t != "any" and value_t != expected_t:
                    raise SemaError(
                        f"outparam[{el_t}] = v: got {value_t}",
                        s.pos,
                        ErrorCode.E_ASSIGN_TYPE,
                    )
            elif obj_t == "dict":
                # "int" doubles as asmpython's unknown sentinel (an untracked
                # element/slot that is a str at runtime), so it's lenient here.
                _ikt = A.expr_type(s.target.index)
                if not _is_dict_key_type(_ikt):
                    raise SemaError(
                        f"dict key must be hashable (str/int/float/bool/tuple/"
                        f"instance), got {_ikt}",
                        s.pos, ErrorCode.E_DICT_KEY_TYPE,
                    )
                dvt = self._dict_value_type(s.target.obj, scope)
                if (
                    dvt not in ("any", "int")
                    and value_t not in ("any", "int")
                    and value_t != dvt
                    # Both instance types: one may be a subtype of the other.
                    and not (dvt.startswith("instance:") and value_t.startswith("instance:"))
                ):
                    if isinstance(s.target.obj, A.Name):
                        # A genuinely heterogeneous dict (e.g. pickle.loads()
                        # building a dict whose values vary by the parsed
                        # tag character) — widen to opaque rather than reject,
                        # same leniency an unannotated dict gets from the
                        # start (see the pin-on-first-write `elif` below).
                        scope.dict_value_types[s.target.obj.name] = "any"
                    else:
                        raise SemaError(
                            f"dict[k] = v: dict values are {dvt}, got {value_t}",
                            s.pos,
                            ErrorCode.E_ASSIGN_TYPE,
                        )
                elif (
                    dvt in ("any", "int")
                    and value_t not in ("any", "int")
                    and isinstance(s.target.obj, A.Name)
                    and s.target.obj.name not in self._explicit_object_dicts
                ):
                    # First concrete write to a dict whose value kind was never
                    # pinned (declared as a bare `dict`, or assigned `{}` --
                    # both resolve to the "any" element-kind sentinel, see
                    # _resolve_scalar_annot) -- same "first write teaches the
                    # element kind" rule as list.append() above. Without this,
                    # every later `d.get(...)`/`d[k]`/`d.values()` keeps
                    # reading the unknown-sentinel default forever, so e.g. a
                    # str-valued dict's reads get treated as raw ints (a
                    # pointer printed as a garbage number, not dereferenced
                    # as a string). Skipped for an EXPLICIT `dict[str, object]`
                    # (in `_explicit_object_dicts`): it must stay "any" so its
                    # boxed values round-trip through type()/isinstance().
                    scope.dict_value_types[s.target.obj.name] = value_t
            elif obj_t == "any":
                pass  # opaque target: accept the index assignment leniently
            elif obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                cls_sig: ClassSig = self.classes.get(cls_name)
                msig = None
                if cls_sig is not None:
                    msig = cls_sig.methods.get("__setitem__")
                if msig is None:
                    raise SemaError(
                        f"'{cls_name}' object does not support index assignment", s.pos,
                        ErrorCode.E_INDEX_ASSIGN,
                    )
                s.target._setitem_class = cls_name  # type: ignore[attr-defined]
            else:
                raise SemaError(f"cannot index a {obj_t}", s.pos, ErrorCode.E_INDEX_OBJECT_TYPE)
            return
        if isinstance(s, A.AttrAssign):
            # cls.field = v inside a @classmethod → rewrite to ClassName.field = v
            if (
                isinstance(s.obj, A.Name)
                and self.classmethod_cls_param is not None
                and s.obj.name == self.classmethod_cls_param
                and self.current_class is not None
            ):
                s.obj.name = self.current_class
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
            obj_t: str = A.expr_type(s.obj)
            # `type` is allowed: `cls.attr = v` sets a class attribute (CPython
            # allows it -- a registry's `cls.__somnia_type__ = name`). ir_lower
            # dispatches it on the runtime class id to the class's mutable
            # namespace dict, like a literal `ClassName.attr = v`.
            if not obj_t.startswith("instance:") and obj_t not in ("any", "module", "int", "type"):
                raise SemaError(
                    f"cannot assign attribute on {obj_t}",
                    s.pos,
                    ErrorCode.E_NO_ATTR,
                )
            if obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                if cls_name in self.classes:
                    self._check_access(cls_name, s.name, is_field=True, pos=s.pos)
                    self._check_immutable(cls_name, s.name, s.pos)
                    resolved = self._resolve_method(cls_name, s.name)
                    if resolved is not None and "property" in resolved[1].decorators:
                        setter_name = self._resolve_setter(cls_name, s.name)
                        if setter_name is None:
                            # A read-only property can never be assigned,
                            # just like in CPython.
                            raise SemaError(
                                f"property {s.name!r} of {cls_name!r} object has no setter",
                                s.pos,
                                ErrorCode.E_PROPERTY_NO_SETTER,
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
            value_t: str = A.expr_type(s.value)
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
                # A pre-pass value of "int" OR "any" is a placeholder, not a
                # real signal, and is safe to refine once the real body check
                # knows better: "int" is _static_value_info's numeric-literal
                # default, and "any" is its opaque-Call/MethodCall/Attr
                # fallback (an initializer like `self.xs = list(...)` reads
                # back "any" from the syntax-only pre-pass, but a concrete
                # "list" once `_check_expr` has actually run on it here).
                # Without "any" in this set, a field first assigned from an
                # opaque-looking call never got its real inferred type, which
                # broke e.g. a generator's synthesized `self._genlist =
                # list(range(n))` field: `len(self._genlist)` then compiled
                # as if `_genlist` were still opaque instead of a real list.
                if s.name not in sig.fields or (
                    sig.fields[s.name] in ("int", "any") and value_t not in ("int", "any")
                ):
                    sig.fields[s.name] = value_t
            return
        if isinstance(s, A.Try):
            self._check_block(s.body, scope)
            for name in s.handler_types:
                self._check_exc_type_name(name, s.pos, scope)
            # `except ... as e` binds the caught exception's message string
            # (asmpython's native exception payload). Codegen relies on this
            # being `str` so `print(e)` prints it correctly.
            if s.bind_name is not None:
                self._require_assignable(s.bind_name, s.pos)
                scope.add(s.bind_name, "str")
            self._check_block(s.handler, scope)
            for types, bind_name, hbody in s.extra_handlers:
                for name in types:
                    self._check_exc_type_name(name, s.pos, scope)
                if bind_name is not None:
                    self._require_assignable(bind_name, s.pos)
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
                and vt != "any"
                and not vt.startswith("instance:")
                and vt != "type"
                and not is_exc_ctor
            ):
                raise SemaError(
                    "raise requires a string message or an exception", s.pos,
                    ErrorCode.E_NOT_AN_EXCEPTION,
                )
            # `raise <opaque value>` (vt == "any"): a re-raise of a caught /
            # dynamically-held exception object (`except ...: raise pending`,
            # a VM forwarding a stored error). ir_lower's _exc_raise_type_id_ir
            # already falls back to EXC_ANY for a value it can't name
            # statically, so lowering handles it; the message comes from the
            # value's own runtime payload / the active-exception forwarding.
            return
        if isinstance(s, A.With):
            self._check_expr(s.expr, scope)
            obj_t: str = A.expr_type(s.expr)
            if obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                enter = self._resolve_method(cls_name, "__enter__")
                exitm = self._resolve_method(cls_name, "__exit__")
                if enter is None or exitm is None:
                    raise SemaError(
                        f"{cls_name!r} object does not support the context "
                        "manager protocol (missing __enter__/__exit__)",
                        s.pos,
                        ErrorCode.E_NOT_A_CONTEXT_MANAGER,
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
                body: list = []
                body.append(cm_assign)
                body.append(enter_stmt)
                body.extend(s.body)
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
            vt: str = A.expr_type(s.value)
            for nm in s.targets:
                self._require_assignable(nm, s.pos)
                scope.add(nm, vt)
            return
        if isinstance(s, A.Global):
            # `global x, y`: just validates that the names exist at module level.
            # Codegen uses this to skip allocating frame slots for them.
            for nm in s.names:
                if nm not in scope.types and nm not in self.global_scope.types:
                    pass  # allow forward-declared globals (assigned before use)
            # `no_global_mutation` extension: record which names THIS
            # function declared `global` for, so _require_assignable can
            # tell "explicitly declared, rebinding is the whole point of
            # this statement" apart from "an undeclared write that happens
            # to share a global's name" -- see self._globals_declared_in.
            if self.in_function is not None:
                self._globals_declared_in.setdefault(self.in_function, set()).update(s.names)
            return
        if isinstance(s, A.Nonlocal):
            # `nonlocal x, y`: closures aren't supported; accept and ignore.
            return
        if isinstance(s, A.Del):
            # `del x` or `del x[k]`: type-check the target expression leniently.
            # Only a bare `del CONST_NAME` (an A.Name target) rebinds/removes
            # the name itself and is rejected; `del const_list[i]` mutates
            # the referenced container, which the constants extension always
            # permits (see ConstDecl's docstring on binding-vs-mutation).
            # `del a, b, c` -> the parser wraps multiple targets in a
            # TupleLit (see parser.py's _parse_del); each element gets the
            # same const-rebind check individually -- checking the whole
            # TupleLit against A.Name would never match (a TupleLit is
            # never itself an A.Name), silently skipping the check for
            # every multi-target del regardless of what it deletes.
            del_targets = s.target.elems if isinstance(s.target, A.TupleLit) else [s.target]
            for tgt in del_targets:
                if isinstance(tgt, A.Name):
                    self._require_assignable(tgt.name, s.pos)
            try:
                self._check_expr(s.target, scope)
            except Exception:
                pass
            return
        if isinstance(s, A.Match):
            # `exhaustive_switch`: require the last case (in source order) to
            # be an unconditional catch-all -- a bare/wildcard MatchCapture
            # with no guard. Checked here, before the reversed-order
            # desugaring loop below, so "last" unambiguously means "last as
            # the user wrote it." A guarded trailing case (`case _ if cond:`)
            # does not count: the guard can still fall through to "no match,"
            # which is exactly what this extension exists to forbid.
            if self._ext_active("exhaustive_switch"):
                last_pattern, last_guard, _ = s.cases[-1] if s.cases else (None, None, None)
                if not (isinstance(last_pattern, A.MatchCapture) and last_guard is None):
                    raise SemaError(
                        "'match' does not cover every case: add a trailing "
                        "unguarded 'case _:' (exhaustive_switch is active)",
                        s.pos,
                        ErrorCode.E_NONEXHAUSTIVE_MATCH,
                    )
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
            f"internal: unhandled stmt {type(s).__name__}", getattr(s, "pos", None),
            ErrorCode.E_INTERNAL_UNHANDLED_NODE,
        )

    # ---- match/case helpers -------------------------------------------------

    def _make_name_ref(self, name: str, pos) -> A.Expr:
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
                        ErrorCode.E_OR_PATTERN_CAPTURE,
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
            _len_ops = self._make_stmt_list()
            _len_ops.append(len_call)
            _len_ops.append(A.IntLit(value=n_fixed, pos=pattern.pos))
            if star_index is None:
                len_test = A.Compare(ops=["=="], operands=_len_ops, pos=pattern.pos)
            else:
                len_test = A.Compare(ops=[">="], operands=_len_ops, pos=pattern.pos)
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
                    _elem_ops = self._make_stmt_list()
                    _elem_ops.append(elem_ref)
                    _elem_ops.append(sub.value)
                    elem_test = A.Compare(ops=["=="], operands=_elem_ops, pos=sub.pos)
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

        if isinstance(pattern, A.MatchMapping):
            map_tests: list = []
            map_pre: list = []
            map_binds: list = []
            subj_ref = self._make_name_ref(subj_name, pos)
            for key, sub in zip(pattern.keys, pattern.patterns):
                key_node = A.StrLit(value=key, pos=pattern.pos)
                # key in subject
                _in_ops = self._make_stmt_list()
                _in_ops.append(key_node)
                _in_ops.append(subj_ref)
                in_test = A.Compare(ops=["in"], operands=_in_ops, pos=pattern.pos)
                map_tests.append(in_test)
                val_ref = A.Subscript(
                    obj=subj_ref,
                    index=key_node,
                    pos=pattern.pos,
                )
                if isinstance(sub, A.MatchCapture):
                    if sub.name != "_":
                        map_binds.append(A.Assign(target=sub.name, value=val_ref, pos=sub.pos))
                elif isinstance(sub, A.MatchValue):
                    _val_ops = self._make_stmt_list()
                    _val_ops.append(val_ref)
                    _val_ops.append(sub.value)
                    val_test = A.Compare(ops=["=="], operands=_val_ops, pos=sub.pos)
                    if isinstance(sub.value, A.StrLit):
                        val_test._map_val_str_cmp = True  # type: ignore[attr-defined]
                    map_tests.append(val_test)
                else:
                    elem_name = f"__match_map_{id(pattern)}_{key}"
                    map_pre.append(A.Assign(target=elem_name, value=val_ref, pos=pattern.pos))
                    sub_pre, sub_test, sub_binds = self._lower_pattern(sub, elem_name, pattern.pos)
                    map_pre.extend(sub_pre)
                    if not (isinstance(sub_test, A.IntLit) and sub_test.value == 1):
                        map_tests.append(sub_test)
                    map_binds.extend(sub_binds)
            return map_pre, self._and_chain(map_tests, pattern.pos), map_binds

        if isinstance(pattern, A.MatchClass):
            cls_name = pattern.cls_name
            # isinstance(subject, ClassName) check.
            _isinst_args = self._make_stmt_list()
            _isinst_args.append(self._make_name_ref(subj_name, pos))
            _isinst_args.append(A.Name(name=cls_name, pos=pattern.pos))
            isinstance_call = A.Call(func="isinstance", args=_isinst_args, pos=pattern.pos)
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
                        ErrorCode.E_MATCH_ARGS_MISSING,
                    )
                if len(pattern.positional) > len(match_args):
                    raise SemaError(
                        f"too many positional patterns for '{cls_name}' "
                        f"(__match_args__ has {len(match_args)} entries)",
                        pattern.pos,
                        ErrorCode.E_MATCH_ARGS_TOO_MANY,
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
            f"internal: unhandled pattern {type(pattern).__name__}", pos,
            ErrorCode.E_INTERNAL_UNHANDLED_NODE,
        )

    def _check_tuple_assign_target(
        self, t: A.Expr, value_t: str, scope: Scope, pos
    ) -> None:
        """Validate one target of a parallel-form TupleAssign against the
        already-checked type of its paired value, and bind `Name` targets
        into scope. Mirrors the equivalent checks in IndexAssign/AttrAssign
        (`xs[0], xs[1] = ...`, `self.x, self.y = ...`)."""
        if isinstance(t, A.Name):
            self._require_assignable(t.name, pos)
            scope.add(t.name, value_t)
            return
        if isinstance(t, A.Subscript):
            self._check_expr(t.obj, scope)
            self._check_expr(t.index, scope)
            obj_t: str = A.expr_type(t.obj)
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
                        ErrorCode.E_ASSIGN_TYPE,
                    )
            elif obj_t == "dict":
                ikt: str = A.expr_type(t.index)
                if not _is_dict_key_type(ikt):
                    raise SemaError(
                        f"dict key must be hashable (str/int/float/bool/tuple/"
                        f"instance), got {ikt}",
                        pos, ErrorCode.E_DICT_KEY_TYPE,
                    )
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
                        ErrorCode.E_ASSIGN_TYPE,
                    )
            elif obj_t == "any":
                pass  # opaque target: accept the index assignment leniently
            else:
                raise SemaError(f"cannot index a {obj_t}", pos, ErrorCode.E_INDEX_OBJECT_TYPE)
            return
        # A.Attr
        if (
            isinstance(t.obj, A.Name)
            and t.obj.name in self.classes
            and self._class_var_type(t.obj.name, t.name) is not None
        ):
            return
        self._check_expr(t.obj, scope)
        obj_t: str = A.expr_type(t.obj)
        # `type` allowed: `cls.attr = v` sets a class attribute (see the
        # matching guard in the A.AttrAssign statement handler).
        if not obj_t.startswith("instance:") and obj_t not in ("any", "module", "int", "type"):
            raise SemaError(f"cannot assign attribute on {obj_t}", pos, ErrorCode.E_NO_ATTR)
        if obj_t.startswith("instance:"):
            cls_name = obj_t.split(":", 1)[1]
            if cls_name in self.classes:
                resolved = self._resolve_method(cls_name, t.name)
                if resolved is not None and "property" in resolved[1].decorators:
                    raise SemaError(
                        f"property {t.name!r} of {cls_name!r} object has no setter",
                        pos,
                        ErrorCode.E_PROPERTY_NO_SETTER,
                    )
                sig = self.classes[cls_name]
                if isinstance(t.obj, A.Name) and t.obj.name == "self":
                    # See the matching comment in _check_stmt's AttrAssign
                    # handling: "any" is as much a placeholder here as "int".
                    if t.name not in sig.fields or (
                        sig.fields[t.name] in ("int", "any") and value_t not in ("int", "any")
                    ):
                        sig.fields[t.name] = value_t

    def _check_expr(self, e: A.Expr, scope: Scope) -> None:
        if isinstance(e, A.IntLit) or isinstance(e, A.FloatLit) or isinstance(e, A.StrLit):
            return
        if isinstance(e, A.Starred):
            # A `*expr` argument normally disappears in
            # `_expand_starred_args`. The one shape that keeps it is a call
            # through a callable VALUE, where the argument count is only known
            # at runtime (`starred_dynamic`) -- the call's own arg loop then
            # reaches the Starred itself, so check what it wraps.
            self._check_expr(e.value, scope)
            return
        if isinstance(e, A.Name):
            if e.name in self.ffi_consts:
                e.inferred_type = self.ffi_consts[e.name].ty
                return
            if e.name in ("min", "max") and e.name not in scope.types:
                e.name = self._ensure_builtin_value_func(e.name, e.pos)
                e.inferred_type = "any"
                return
            # A class name used as a value (passed to isinstance, stored, etc.)
            # is a first-class "type" object. Builtin exception classes and
            # builtin scalar/container type names (e.g. `{"type": str}`,
            # mimicking argparse's `type=str`) count too.
            #
            # A local binding shadows all of these (Python's LEGB rule: Local
            # before Builtin). Without the `not in scope.types` guard a
            # parameter or variable named like a builtin -- e.g.
            # `def basicConfig(format: str = ""): ... len(format)` -- resolves
            # the *name* to the builtin instead of the bound value, so its type
            # is lost ("type"/"any") and `len(format)` reads garbage. The
            # `min`/`max` and module-function checks around here already guard
            # this way.
            if e.name not in scope.types and (
                e.name in self.classes
                or e.name in BUILTIN_EXCEPTIONS
                or e.name in BUILTIN_TYPE_NAMES
            ):
                e.inferred_type = "type"
                return
            # Builtin callables are also valid first-class values, for
            # example when an interpreter seeds a globals dictionary with
            # ``{"print": print, "len": len}``. Calls still use the normal
            # builtin lowering when the name appears in call position.
            if e.name in BUILTIN_VALUE_NAMES and e.name not in scope.types:
                e.inferred_type = "any"
                return
            # A module-level function used as a value (passed, stored in a var).
            # Scope binding takes priority: if the user named a variable the same
            # as a merged stdlib function (e.g. `log = logging.getLogger(...)`
            # shadowing `logging.log`), the variable's type wins.
            if e.name in self.funcs and e.name not in scope.types:
                e.inferred_type = "any"
                return
            if e.name not in scope.types:
                if self.in_lifted:
                    e.inferred_type = "any"
                    return
                raise SemaError(f"undefined variable {e.name!r}", e.pos, ErrorCode.E_UNDEFINED_NAME)
            e.inferred_type = scope.types[e.name]
            if e.inferred_type == "list":
                e.list_el_type = scope.list_el_types.get(e.name, "int")
                e.list_el_value_type = scope.list_el_value_types.get(e.name, "int")
                if e.list_el_type == "tuple":
                    # A list[tuple] READ has to carry its per-slot kinds onto
                    # the node, not just leave them in the scope: ir_lower's
                    # repr reads them off the expression. Without this a
                    # list of pairs held in a VARIABLE (as opposed to written
                    # inline at the print) had no shape, so its repr fell back
                    # to `_abi_list_repr`'s dict-items assumption and any pair
                    # that isn't (str, int) formatted its leading slot as a
                    # string POINTER -- a segfault, not wrong output.
                    e.el_tuple_types = list(  # type: ignore[attr-defined]
                        scope.list_el_tuple_types.get(e.name, [])
                    )
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
            ot: str = A.expr_type(e.operand)
            if ot.startswith("instance:") and e.op in DUNDER_UNARY:
                mname = DUNDER_UNARY[e.op]
                cls_name = ot.split(":", 1)[1]
                resolved = self._resolve_method(cls_name, mname)
                if resolved is not None:
                    owner: str = resolved[0]
                    sig: FuncSig = resolved[1]
                    e.dunder_owner = owner  # type: ignore
                    e.dunder_method = mname  # type: ignore
                    if sig.ret_type is not None:
                        rt: tuple = sig.ret_type  # type: ignore
                        ty: str = rt[0]
                        el = rt[1]
                        e.inferred_type = ty  # type: ignore
                        if ty == "list" and el is not None:
                            e.list_el_type = el  # type: ignore
                    else:
                        e.inferred_type = "any"  # type: ignore
            return
        if isinstance(e, A.BinOp):
            self._check_expr(e.left, scope)
            self._check_expr(e.right, scope)
            lt: str = A.expr_type(e.left)
            rt: str = A.expr_type(e.right)
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
                    owner: str = resolved[0]
                    sig: FuncSig = resolved[1]
                    if sig.arity != 2:
                        raise SemaError(
                            f"{owner}.{sig.name}() must take exactly (self, other)",
                            e.pos,
                            ErrorCode.E_DUNDER_SIGNATURE,
                        )
                    e.dunder_owner = owner  # type: ignore
                    e.dunder_method = sig.name  # type: ignore
                    e.dunder_reflected = reflected  # type: ignore
                    if sig.ret_type is not None:
                        rt2: tuple = sig.ret_type  # type: ignore
                        ty: str = rt2[0]
                        el = rt2[1]
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
                    ErrorCode.E_BINARY_OP_TYPE,
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
                left_el = self._list_el_type(e.left, scope) if lt == "list" else "int"
                right_el = self._list_el_type(e.right, scope) if rt == "list" else "int"
                chosen_el = left_el
                if chosen_el in ("int", "?") or (chosen_el == "any" and right_el not in ("int", "?")):
                    chosen_el = right_el
                elif right_el not in ("int", "?", "any") and chosen_el != right_el:
                    chosen_el = "any"
                e.list_el_type = chosen_el  # type: ignore[attr-defined]
                if chosen_el == "tuple":
                    left_tup = self._list_el_tuple_types(e.left, scope) if lt == "list" else []
                    right_tup = self._list_el_tuple_types(e.right, scope) if rt == "list" else []
                    if left_tup and right_tup and left_tup == right_tup:
                        e.el_tuple_types = list(left_tup)  # type: ignore[attr-defined]
                    elif left_tup:
                        e.el_tuple_types = list(left_tup)  # type: ignore[attr-defined]
                    elif right_tup:
                        e.el_tuple_types = list(right_tup)  # type: ignore[attr-defined]
                elif chosen_el in ("list", "dict"):
                    left_inner = self._list_el_value_type(e.left, scope) if lt == "list" else "int"
                    right_inner = self._list_el_value_type(e.right, scope) if rt == "list" else "int"
                    chosen_inner = left_inner
                    if chosen_inner in ("int", "?") or (chosen_inner == "any" and right_inner not in ("int", "?")):
                        chosen_inner = right_inner
                    elif right_inner not in ("int", "?", "any") and chosen_inner != right_inner:
                        chosen_inner = "any"
                    e.el_value_type = chosen_inner  # type: ignore[attr-defined]
                return
            # List repetition: [x] * n  or  n * [x]  -> list.
            if e.op == "*" and (
                (lt == "list" and rt in ("int", "any")) or
                (rt == "list" and lt in ("int", "any"))
            ):
                e.inferred_type = "list"  # type: ignore
                list_side = e.left if lt == "list" else e.right
                e.list_el_type = self._list_el_type(list_side, scope)  # type: ignore
                return
            # Numeric-only ops; reject lists/dicts/instances.
            for side, t in (("left", lt), ("right", rt)):
                if t not in ("int", "float"):
                    raise SemaError(
                        f"unsupported operand type for {e.op}: {t}",
                        e.pos,
                        ErrorCode.E_UNARY_OP_TYPE,
                    )
            # Bitwise / shift can't take floats.
            if e.op in ("&", "|", "^", "<<", ">>"):
                if "float" in (lt, rt):
                    raise SemaError(
                        f"bitwise/shift operator {e.op!r} requires int operands",
                        e.pos,
                        ErrorCode.E_BINARY_OP_TYPE,
                    )
            return
        if isinstance(e, A.Compare):
            # `enum` extension: reject `Color.RED == Direction.NORTH`-style
            # cross-enum-type comparisons. Must run BEFORE the operands are
            # individually checked below -- `self._check_expr` on an
            # `EnumName.MEMBER` Attr folds it to a plain IntLit in place
            # (see the A.Attr handler), which is exactly what erases the
            # "this came from an enum" information this check needs. Only
            # the raw, pre-fold AST shape still has it.
            if self.enum_types and any(op in ("==", "!=") for op in e.ops):
                operand_enum_names: list = []
                for op_expr in e.operands:
                    en = None
                    if (
                        isinstance(op_expr, A.Attr)
                        and isinstance(op_expr.obj, A.Name)
                        and op_expr.obj.name in self.enum_types
                    ):
                        en = op_expr.obj.name
                    operand_enum_names.append(en)
                for i, op in enumerate(e.ops):
                    if op not in ("==", "!="):
                        continue
                    a_en, b_en = operand_enum_names[i], operand_enum_names[i + 1]
                    if a_en is not None and b_en is not None and a_en != b_en:
                        raise SemaError(
                            f"cannot compare {a_en} and {b_en}: they are "
                            f"different enum types",
                            e.pos,
                            ErrorCode.E_ENUM_TYPE_MISMATCH,
                        )
            for op in e.operands:
                self._check_expr(op, scope)
            for i, op in enumerate(e.ops):
                lt: str = A.expr_type(e.operands[i])
                rt: str = A.expr_type(e.operands[i + 1])
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
                        if el_t == "?":
                            el_t = "any"
                        if lt != el_t and el_t != "any" and lt != "int":
                            raise SemaError(
                                f"'{op}': needle is {lt} but list elements are {el_t}",
                                e.pos,
                                ErrorCode.E_CONTAINS_TYPE_MISMATCH,
                            )
                        continue
                    if rt == "dict":
                        if not _is_dict_key_type(lt):
                            raise SemaError(
                                f"'{op}' on dict needs a hashable key "
                                f"(str/int/float/bool/tuple/instance), got {lt}",
                                e.pos,
                                ErrorCode.E_DICT_KEY_TYPE,
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
                        kinds: list[str] = []
                        for t in ets:
                            if t != "any" and t not in kinds:
                                kinds.append(t)
                        if len(kinds) > 1:
                            raise SemaError(
                                "'in' on a heterogeneous tuple is unsupported",
                                e.pos,
                                ErrorCode.E_HETEROGENEOUS_TUPLE,
                            )
                        # "int" doubles as the unknown sentinel, so it's a lenient
                        # needle (asmpython's shallow inference types many strings
                        # as int).
                        if kinds and lt not in ("any", "int") and lt not in kinds:
                            only = kinds[0]
                            raise SemaError(
                                f"'{op}': needle is {lt} but tuple elements are {only}",
                                e.pos,
                                ErrorCode.E_CONTAINS_TYPE_MISMATCH,
                            )
                        continue
                    if rt == "set":
                        # `x in {…}`: sets only model membership; the element
                        # kind isn't tracked, so accept any needle.
                        continue
                    if rt in ("any", "int"):
                        # Membership against an opaque value (`any`) or the
                        # unknown-`int` sentinel: dict-backed at runtime.
                        continue
                    if rt.startswith("instance:"):
                        # `x in obj` on a user instance: dispatch to __contains__
                        # if the class defines it; error otherwise.
                        cls_name = rt.split(":", 1)[1]
                        resolved = self._resolve_method(cls_name, "__contains__")
                        if resolved is not None and len(e.ops) == 1:
                            owner, sig = resolved
                            e.dunder_contains_owner = owner  # type: ignore[attr-defined]
                            e.dunder_contains_negate = (op == "not in")  # type: ignore[attr-defined]
                        else:
                            raise SemaError(
                                f"'{op}': {cls_name} does not define __contains__",
                                e.pos,
                                ErrorCode.E_NO_CONTAINS_METHOD,
                            )
                        continue
                    raise SemaError(
                        f"'{op}' not supported between {lt} and {rt}",
                        e.pos,
                        ErrorCode.E_BINARY_OP_TYPE,
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
                                ErrorCode.E_DUNDER_SIGNATURE,
                            )
                        e.dunder_owner = owner  # type: ignore
                        e.dunder_method = "__eq__"  # type: ignore
                        e.dunder_negate = (op == "!=")  # type: ignore
                    continue
                if op in ("<", "<=", ">", ">=") and (
                    lt.startswith("instance:") or rt.startswith("instance:")
                ):
                    # Ordering comparisons dispatch to __lt__/__le__/__gt__/__ge__
                    # with reflected fallback, like DUNDER_BINOP's resolution.
                    _DUNDER_CMP: dict = {
                        "<": ("__lt__", "__gt__"),
                        "<=": ("__le__", "__ge__"),
                        ">": ("__gt__", "__lt__"),
                        ">=": ("__ge__", "__le__"),
                    }
                    fwd_m, rfl_m = _DUNDER_CMP[op]
                    cmp_resolved = None
                    cmp_reflected = False
                    if lt.startswith("instance:"):
                        cmp_resolved = self._resolve_method(
                            lt.split(":", 1)[1], fwd_m
                        )
                    if cmp_resolved is None and rt.startswith("instance:"):
                        cmp_resolved = self._resolve_method(
                            rt.split(":", 1)[1], rfl_m
                        )
                        cmp_reflected = cmp_resolved is not None
                    if cmp_resolved is not None and len(e.ops) == 1:
                        owner, _ = cmp_resolved
                        e.dunder_owner = owner  # type: ignore
                        e.dunder_method = rfl_m if cmp_reflected else fwd_m  # type: ignore
                        e.dunder_reflected = cmp_reflected  # type: ignore
                    continue
                if "str" in (lt, rt):
                    if op not in ("==", "!=", "<", "<=", ">", ">="):
                        raise SemaError(
                            f"string comparison does not support {op!r}",
                            e.pos,
                            ErrorCode.E_STRING_COMPARISON_OP,
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
                            ErrorCode.E_UNCOMPARABLE_TYPES,
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
            elif "float" not in (bt, ot):
                # Two different pointer-sized kinds (e.g. `tuple(xs) if c else
                # set(xs)`, a list-vs-dict guard, `some_instance if c else
                # "name"`): all are 8-byte pointers (heap object or str label),
                # so the result shares one uniform slot -- take "any" rather
                # than rejecting, the same leniency the heterogeneous-list and
                # DictLit-value rules already apply. The int case is already
                # handled above (int doubles as the unknown sentinel and picks
                # the concrete arm), so neither side is int here. Only float
                # (an xmm-register value, a genuine register-class clash with
                # every pointer-sized kind) stays a hard mismatch. A str arm
                # collapsing to "any" means a later use reads it opaquely (the
                # same well-understood "any"-formatting leniency everywhere),
                # not a crash.
                e.inferred_type = "any"
            else:
                raise SemaError(
                    f"conditional expression arms have mismatched types ({bt} vs {ot})",
                    e.pos,
                    ErrorCode.E_TYPE_MISMATCH,
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
                if isinstance(el, A.Starred):
                    self._check_expr(el.value, scope)
                    spread_t = A.expr_type(el.value)
                    if spread_t == "list":
                        et = self._list_el_type(el.value, scope)
                    elif spread_t == "tuple":
                        ets = self._tuple_elem_types(el.value, scope)
                        et = ets[0] if ets and _all_same(ets) else "any"
                    elif spread_t == "any":
                        et = "any"
                    else:
                        raise SemaError(
                            f"list unpacking requires a list or tuple, got {spread_t}",
                            el.pos,
                            ErrorCode.E_SPREAD_NOT_ITERABLE,
                        )
                else:
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
                        ErrorCode.E_LIST_ELEMENT_TYPE_UNSUPPORTED,
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
                    # Two different element kinds. Every pointer-sized kind --
                    # "int" (a raw integer, or asmpython's unknown sentinel) and
                    # every heap-pointer kind (list/dict/tuple/set/instance) --
                    # occupies the same uniform 8-byte slot, so any MIX of them
                    # collapses to opaque ("any") rather than erroring: e.g. a
                    # list holding both tuples and dicts (a bytecode constant
                    # pool, an AST node list), or an ELF header literal mixing a
                    # nested byte-array list with scalar int fields. Reads off
                    # such a list stay lenient ("any"). The same leniency DictLit
                    # already applies to its values. Only "float" (lives in an
                    # xmm register, a different class than every pointer-sized
                    # kind) and "str" (its "any"-typed read sites assume a real
                    # string label) stay hard errors when mixed with a different
                    # kind -- a genuine register-class / representation clash.
                    if "float" not in (seen, et) and "str" not in (seen, et):
                        seen = "any"
                        continue
                    raise SemaError(
                        f"mixed list element types ({seen} and {et}); "
                        "mixed-type lists need a tagged-value runtime, not yet implemented",
                        getattr(el, "pos", e.pos),
                        ErrorCode.E_HETEROGENEOUS_LIST,
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
            # `[elt for i, x in enumerate(xs) ...]` — treat like the For-loop
            # enumerate special case: bind index as int, element from xs.
            if (
                isinstance(e.iter, A.Call)
                and e.iter.func == "enumerate"
                and len(e.iter.args) >= 1
                and e.targets
                and len(e.targets) == 2
            ):
                inner = e.iter.args[0]
                self._check_expr(inner, scope)
                child = Scope()
                child.types.update(scope.types)
                child.list_el_types.update(scope.list_el_types)
                child.list_el_tuple_types.update(scope.list_el_tuple_types)
                child.dict_value_types.update(scope.dict_value_types)
                child.dict_inner_value_types.update(scope.dict_inner_value_types)
                child.tuple_elem_types.update(scope.tuple_elem_types)
                idx_name: str = e.targets[0] if e.targets else ""
                el_name: str = e.targets[1] if len(e.targets) > 1 else ""
                if idx_name:
                    child.add(idx_name, "int")
                if el_name:
                    el_ty = self._iter_element_type(inner, scope)
                    if el_ty == "tuple":
                        child.add(el_name, "tuple", tuple_types=self._tuple_elem_types(inner, scope))
                    elif el_ty == "dict":
                        inner_val = self._list_el_value_type(inner, scope)
                        child.add(
                            el_name,
                            "dict",
                            value_type=inner_val if inner_val != "int" else "any",
                        )
                    elif el_ty == "list":
                        inner_el = self._list_el_value_type(inner, scope)
                        child.add(
                            el_name,
                            "list",
                            el_type=inner_el if inner_el != "int" else "any",
                        )
                    else:
                        child.add(el_name, el_ty)
                loop_vars = set()
                if idx_name:
                    loop_vars.add(idx_name)
                if el_name:
                    loop_vars.add(el_name)
                if e.cond is not None:
                    self._check_expr(e.cond, child)
                self._check_expr(e.elt, child)
                e.inferred_type = "list"
                e.list_el_type = A.expr_type(e.elt)
                if e.list_el_type == "tuple":
                    # The general comprehension path below records the element
                    # tuple's per-slot kinds; this enumerate fast path did not,
                    # so `[(i, v) for i, v in enumerate(xs)]` produced a
                    # list[tuple] with NO shape. Its repr then fell back to
                    # `_abi_list_repr`'s dict-items (str, int) assumption and
                    # formatted the leading int as a string POINTER -- a
                    # segfault.
                    e.el_tuple_types = self._tuple_elem_types(e.elt, child)
                self._merge_walrus_bindings(scope, child, loop_vars)
                return
            self._check_expr(e.iter, scope)
            it_t = A.expr_type(e.iter)
            # Element type the loop variable takes from the iterable.
            if it_t == "list":
                el = self._list_el_type(e.iter, scope)
            elif it_t in ("str", "dict", "set"):
                # str chars / dict keys / set members are all str-shaped
                # values in this backend (`_abi_dict_keys` yields the keys as
                # strings; A.For's own generic set/dict iteration types its
                # loop var "str" identically). `set` previously bound the
                # loop var "any", which mis-typed every use of it in the
                # element expression (e.g. `len(x)` read garbage).
                el = "str"
            elif it_t == "tuple":
                ets = A.tuple_element_types(e.iter)
                el = ets[0] if ets else "int"
            elif it_t == "any":
                el = "any"
            elif it_t.startswith("instance:"):
                cls_name = it_t.split(":", 1)[1]
                cls_sig: ClassSig = self.classes.get(cls_name)
                if cls_sig is None or "__iter__" not in cls_sig.methods or "__next__" not in cls_sig.methods:
                    raise SemaError(
                        f"cannot iterate a {it_t} in a comprehension: "
                        f"class must define __iter__ and __next__",
                        e.pos,
                        ErrorCode.E_ITER_TYPE,
                    )
                next_sig = cls_sig.methods.get("__next__")
                el = (next_sig.ret_type[0] if next_sig and next_sig.ret_type else "any")
            else:
                raise SemaError(f"cannot iterate a {it_t} in a comprehension", e.pos, ErrorCode.E_ITER_TYPE)
            # A child scope so the loop variable doesn't leak.
            child = Scope()
            child.types.update(scope.types)
            child.list_el_types.update(scope.list_el_types)
            child.list_el_tuple_types.update(scope.list_el_tuple_types)
            child.dict_value_types.update(scope.dict_value_types)
            child.dict_inner_value_types.update(scope.dict_inner_value_types)
            child.tuple_elem_types.update(scope.tuple_elem_types)
            self._bind_comprehension_targets(e, el, child)
            # When the outer element is itself a list (list[list[T]]), propagate
            # the inner element type so `_list_el_type(var, child)` returns T.
            if el == "list" and not e.targets:
                inner_el = self._list_el_value_type(e.iter, scope)
                if inner_el != "int":
                    child.list_el_types[e.var] = inner_el
            loop_vars = set(self._flat_target_names(e.targets)) if e.targets else {e.var}
            if e.cond is not None:
                self._check_expr(e.cond, child)
            ef_vars: list = getattr(e, "extra_for_vars", [])
            ef_targets_list: list = getattr(e, "extra_for_targets", [])
            ef_iters: list = getattr(e, "extra_for_iters", [])
            ef_conds: list = getattr(e, "extra_for_conds", [])
            for ef_n in range(len(ef_iters)):
                ef_evar = ef_vars[ef_n] if ef_n < len(ef_vars) else ""
                ef_emulti = ef_targets_list[ef_n] if ef_n < len(ef_targets_list) else []
                ef_iter = ef_iters[ef_n]
                ef_cond = ef_conds[ef_n] if ef_n < len(ef_conds) else None
                self._check_expr(ef_iter, child)
                ef_it_t = A.expr_type(ef_iter)
                if ef_it_t == "list":
                    ef_el = self._list_el_type(ef_iter, child)
                elif ef_it_t in ("str", "dict"):
                    ef_el = "str"
                elif ef_it_t == "any":
                    ef_el = "any"
                else:
                    ef_el = "int"
                if ef_emulti:
                    for tgt in ef_emulti:
                        if isinstance(tgt, str):
                            child.add(tgt, ef_el)
                            loop_vars.add(tgt)
                elif ef_evar:
                    child.add(ef_evar, ef_el)
                    loop_vars.add(ef_evar)
                    # Propagate inner element type for list[list[T]] case
                    if ef_el == "list":
                        ef_inner = self._list_el_value_type(ef_iter, child)
                        if ef_inner != "int":
                            child.list_el_types[ef_evar] = ef_inner
                    elif ef_el == "dict":
                        ef_inner = self._list_el_value_type(ef_iter, child)
                        child.dict_value_types[ef_evar] = (
                            ef_inner if ef_inner != "int" else "any"
                        )
                if ef_cond is not None:
                    self._check_expr(ef_cond, child)
            self._check_expr(e.elt, child)
            e.inferred_type = "list"
            e.list_el_type = A.expr_type(e.elt)
            if e.list_el_type == "tuple":
                e.el_tuple_types = self._tuple_elem_types(e.elt, child)
            self._merge_walrus_bindings(scope, child, loop_vars)
            return
        if isinstance(e, A.DictComprehension):
            # enumerate(iterable) special case: `{k: i for i, k in enumerate(xs)}`
            if (
                isinstance(e.iter, A.Call)
                and e.iter.func == "enumerate"
                and len(e.iter.args) >= 1
                and e.targets
                and len(e.targets) == 2
            ):
                inner = e.iter.args[0]
                self._check_expr(inner, scope)
                child = Scope()
                child.types.update(scope.types)
                child.list_el_types.update(scope.list_el_types)
                child.list_el_tuple_types.update(scope.list_el_tuple_types)
                child.dict_value_types.update(scope.dict_value_types)
                child.dict_inner_value_types.update(scope.dict_inner_value_types)
                child.tuple_elem_types.update(scope.tuple_elem_types)
                idx_name: str = e.targets[0] if e.targets else ""
                el_name: str = e.targets[1] if len(e.targets) > 1 else ""
                if idx_name:
                    child.add(idx_name, "int")
                if el_name:
                    el_ty = self._iter_element_type(inner, scope)
                    if el_ty == "tuple":
                        child.add(el_name, "tuple", tuple_types=self._tuple_elem_types(inner, scope))
                    elif el_ty == "dict":
                        inner_val = self._list_el_value_type(inner, scope)
                        child.add(
                            el_name,
                            "dict",
                            value_type=inner_val if inner_val != "int" else "any",
                        )
                    elif el_ty == "list":
                        inner_el = self._list_el_value_type(inner, scope)
                        child.add(
                            el_name,
                            "list",
                            el_type=inner_el if inner_el != "int" else "any",
                        )
                    else:
                        child.add(el_name, el_ty)
                loop_vars = set()
                if idx_name:
                    loop_vars.add(idx_name)
                if el_name:
                    loop_vars.add(el_name)
                if e.cond is not None:
                    self._check_expr(e.cond, child)
                self._check_expr(e.key, child)
                self._check_expr(e.value, child)
                vt = A.expr_type(e.value)
                e.inferred_type = "dict"
                e.value_type = vt if vt != "any" else "int"
                self._merge_walrus_bindings(scope, child, loop_vars)
                return
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
                    f"cannot iterate a {it_t} in a dict comprehension", e.pos,
                    ErrorCode.E_ITER_TYPE,
                )
            child = Scope()
            child.types.update(scope.types)
            child.list_el_types.update(scope.list_el_types)
            child.list_el_tuple_types.update(scope.list_el_tuple_types)
            child.dict_value_types.update(scope.dict_value_types)
            child.dict_inner_value_types.update(scope.dict_inner_value_types)
            child.tuple_elem_types.update(scope.tuple_elem_types)
            self._bind_comprehension_targets(e, el, child)
            loop_vars = set(self._flat_target_names(e.targets)) if e.targets else {e.var}
            if e.cond is not None:
                self._check_expr(e.cond, child)
            self._check_expr(e.key, child)
            # Dict keys are stored as strings at the runtime level. Non-str
            # keys work for LOOKUP (subscript / `in` / literal construction --
            # ir_lower's `_lower_dict_key` encodes each to a canonical string),
            # but a comprehension exists to be MATERIALIZED and then usually
            # iterated/printed, and iteration yields the stored strings, not
            # the original keys (`{v: k ...}` with int `v` would print/repr as
            # `{'1': ...}`, not `{1: ...}`). So a comprehension key stays
            # restricted to str/any -- where store and iterate agree -- rather
            # than silently producing a value that round-trips wrong. (A
            # genuinely non-str-keyed dict that is only ever looked up, like an
            # lru_cache memo, is still fully supported via the imperative
            # `cache[k] = v` / `k in cache` spellings, which don't iterate.)
            if A.expr_type(e.key) not in ("str", "any"):
                raise SemaError(
                    "dict comprehension keys must be strings "
                    "(other key kinds work for lookup but not when the "
                    "comprehension's result is iterated/printed)",
                    getattr(e.key, "pos", e.pos),
                    ErrorCode.E_DICT_KEY_TYPE,
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
                    ErrorCode.E_DICT_VALUE_TYPE_UNSUPPORTED,
                )
            e.inferred_type = "dict"
            e.value_type = vt if vt != "any" else "int"
            self._merge_walrus_bindings(scope, child, loop_vars)
            return
        if isinstance(e, A.DictLit):
            for k, v in zip(e.keys, e.values):
                if isinstance(k, A.Name) and k.name == "**":
                    # `**other` (PEP 448 dict unpacking): `other` must itself
                    # be dict-typed (or opaque).
                    self._check_expr(v, scope)
                    vt = A.expr_type(v)
                    if vt not in ("dict", "any"):
                        raise SemaError(
                            f"dict unpacking requires a dict (got {vt})",
                            getattr(v, "pos", e.pos),
                            ErrorCode.E_DICT_UNPACK_TYPE,
                        )
                    continue
                self._check_expr(k, scope)
                _klt = A.expr_type(k)
                if not _is_dict_key_type(_klt):
                    raise SemaError(
                        f"dict key must be hashable (str/int/float/bool/tuple/"
                        f"instance), got {_klt}",
                        getattr(k, "pos", e.pos),
                        ErrorCode.E_DICT_KEY_TYPE,
                    )
            # Dict values must be homogeneous: all int, all str, all float, all
            # instances of one class, or all of one pointer-sized collection
            # kind (dict / list / set / tuple). Nested collections are stored
            # as heap pointers, which fit the same uniform 8-byte slot. The
            # value kind is tracked on the DictLit so codegen / iteration / a
            # chained read (`d[k][k2]`) can recover it.
            seen_v: str | None = None
            saw_opaque_value = False
            for k, v in zip(e.keys, e.values):
                if isinstance(k, A.Name) and k.name == "**":
                    # A `**other` spread contributes `other`'s value kind too,
                    # so e.g. `{**d1, "x": 1}` where `d1: dict[str, str]` and
                    # the literal key is `int` collapses to "any" below, same
                    # as any other value-kind mismatch. An opaque `other`
                    # ("any"-typed dict) is compatible with any value kind.
                    vt = "any" if A.expr_type(v) == "any" else self._dict_value_type(v, scope)
                else:
                    self._check_expr(v, scope)
                    vt = A.expr_type(v)
                    # A callable value (lambda / function reference) is a code
                    # pointer, so it fits the dict's uniform 8-byte value slot
                    # like any other pointer. Recording it as `callable:<ret>`
                    # is what lets `handlers[k](...)` be recognized as a call
                    # -- see `_callable_type_of`.
                    _cv = self._callable_type_of(v, scope)
                    if _cv is not None:
                        vt = _cv
                    if vt not in (
                        "int",
                        "str",
                        "float",
                        "any",
                        "tuple",
                        "dict",
                        "list",
                        "set",
                        "type",
                    ) and not vt.startswith("instance:") and not vt.startswith("callable:"):
                        raise SemaError(
                            f"dict value of type {vt} is not supported yet",
                            getattr(v, "pos", e.pos),
                            ErrorCode.E_DICT_VALUE_TYPE_UNSUPPORTED,
                        )
                if vt == "any":
                    saw_opaque_value = True
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
                            ErrorCode.E_DICT_VALUE_TYPE_MIXED,
                        )
                    # Two callables that disagree only on their RETURN kind are
                    # still both callable: keep the values callable (with an
                    # opaque result) rather than collapsing to plain "any",
                    # which would lose the callability itself.
                    if seen_v.startswith("callable:") and vt.startswith("callable:"):
                        seen_v = "callable:any"
                    else:
                        seen_v = "any"
            e.value_type = seen_v if seen_v is not None else ("any" if saw_opaque_value else "int")
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
                        ErrorCode.E_TUPLE_ELEMENT_TYPE_UNSUPPORTED,
                    )
                ets.append(et)
            e.elem_types = ets
            return
        if isinstance(e, A.SetLit):
            # A `{a, b, ...}` set literal. Elements are checked but their kind
            # isn't tracked (set membership is the only operation modelled);
            # `expr_type` already reports a SetLit as "set". The backing store
            # is a str-keyed dict; `int` elements are accepted by converting
            # to their decimal string at codegen time (see _gen_set_lit).
            for el in e.elems:
                self._check_expr(el, scope)
                et = A.expr_type(el)
                if et not in ("str", "int", "any", "tuple"):
                    raise SemaError(
                        f"set elements of type {et} are not supported yet "
                        "(sets are str/int-keyed in v1)",
                        getattr(el, "pos", e.pos),
                        ErrorCode.E_SET_KEY_TYPE,
                    )
            return
        if isinstance(e, A.Subscript):
            self._check_expr(e.obj, scope)
            obj_t = A.expr_type(e.obj)
            if isinstance(e.index, A.Slice):
                if obj_t not in ("str", "list", "any", "int"):
                    raise SemaError(f"slicing not supported on {obj_t}", e.pos, ErrorCode.E_INDEX_OBJECT_TYPE)
                if e.index.start is not None:
                    self._check_expr(e.index.start, scope)
                    if A.expr_type(e.index.start) not in ("int", "any"):
                        raise SemaError("slice start must be an int", e.pos, ErrorCode.E_INDEX_TYPE)
                if e.index.stop is not None:
                    self._check_expr(e.index.stop, scope)
                    if A.expr_type(e.index.stop) not in ("int", "any"):
                        raise SemaError("slice stop must be an int", e.pos, ErrorCode.E_INDEX_TYPE)
                if e.index.step is not None:
                    self._check_expr(e.index.step, scope)
                    if A.expr_type(e.index.step) not in ("int", "any"):
                        raise SemaError("slice step must be an int", e.pos, ErrorCode.E_INDEX_TYPE)
                if obj_t == "any":
                    e.inferred_type = "any"
                    return
                if obj_t == "list":
                    # List slice preserves element type.
                    e.inferred_type = "list"
                    # Propagate element type onto the Subscript so codegen and
                    # downstream `_list_el_type` see the right kind.
                    e.list_el_type = self._list_el_type(e.obj, scope)
                    if e.list_el_type == "tuple":
                        # ...and the per-slot shape with it. A slice of a
                        # list[tuple] is still a list of the SAME tuples, but
                        # without their shape the result's repr falls back to
                        # `_abi_list_repr`'s dict-items (str, int) assumption
                        # and formats a leading int as a string pointer --
                        # `ps[:1]` segfaulted where `ps` printed fine.
                        e.el_tuple_types = self._list_el_tuple_types(  # type: ignore[attr-defined]
                            e.obj, scope
                        )
                else:
                    e.inferred_type = "str"
                return
            self._check_expr(e.index, scope)
            if obj_t == "list":
                # Normalize a bare class-name element kind to `instance:<Class>`
                # so a method call on the read-out element resolves: indexing a
                # `list[Handler]` yields an INSTANCE of Handler, but
                # `_list_el_type` returns the annotation's bare class name, and
                # `h = xs[i]; h.method()` then failed with "[E113] <Class> has no
                # method" (the bare name reads as a type, not an instance).
                # Scalars/containers/any/already-`instance:` pass through.
                e.inferred_type = self._normalize_instance_type(
                    self._list_el_type(e.obj, scope)
                )
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
                    raise SemaError("tuple index must be an int", e.pos, ErrorCode.E_INDEX_TYPE)
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
                            f"tuple index {idx} out of range for {n}-tuple", e.pos,
                            ErrorCode.E_TUPLE_INDEX_RANGE,
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
                        ErrorCode.E_TUPLE_INDEX_NOT_CONST,
                    )
            elif obj_t == "inparam":
                # `items[i]`: read the i'th T-sized element from a caller-
                # owned array pointer (an exported function's inparam[T]
                # parameter -- see outparam's IndexAssign counterpart for
                # the write-through direction). Unlike outparam's single
                # pointee, a real index expression (a loop counter, not
                # just literal 0) is legitimate here -- see ir_lower.py's
                # lowering for the pointer-arithmetic this implies.
                if A.expr_type(e.index) not in ("int", "any"):
                    raise SemaError(
                        "inparam[T] index must be an int", e.pos, ErrorCode.E_INDEX_TYPE
                    )
                el_t = self._inparam_el_type(e.obj, scope)
                # inparam[int8]'s pointee is a raw C ABI byte width, not a
                # real asmpython value type -- items[i] still reads out as
                # an ordinary int (0-255), same as C's `uint8_t` widening
                # to `int` on ordinary use. Only "int"/"float" are real
                # inferred_type values elsewhere in sema/codegen.
                e.inferred_type = (
                    "int" if el_t in ("int8", "int32") else el_t
                )
            elif obj_t == "dict":
                # "int" doubles as the unknown sentinel; lenient (see above).
                _rkt = A.expr_type(e.index)
                if not _is_dict_key_type(_rkt):
                    raise SemaError(
                        f"dict key must be hashable (str/int/float/bool/tuple/"
                        f"instance), got {_rkt}",
                        e.pos, ErrorCode.E_DICT_KEY_TYPE,
                    )
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
                    raise SemaError("string index must be an int", e.pos, ErrorCode.E_INDEX_TYPE)
                e.inferred_type = "str"
            elif obj_t == "any":
                # Indexing an opaque value stays opaque.
                e.inferred_type = "any"
            elif obj_t.startswith("instance:"):
                cls_name = obj_t.split(":", 1)[1]
                cls_sig: ClassSig = self.classes.get(cls_name)
                msig = None
                if cls_sig is not None:
                    msig = cls_sig.methods.get("__getitem__")
                if msig is None:
                    raise SemaError(
                        f"'{cls_name}' object does not support indexing", e.pos,
                        ErrorCode.E_INDEX_OBJECT_TYPE,
                    )
                # Mark so codegen translates this subscript into a __getitem__ call.
                e._getitem_class = cls_name  # type: ignore[attr-defined]
                if msig.ret_type is not None:
                    rt3: tuple = msig.ret_type  # type: ignore[misc]
                    ty: str = rt3[0]
                    el = rt3[1]
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el  # type: ignore[attr-defined]
                else:
                    e.inferred_type = "int"
            else:
                raise SemaError(f"cannot index a {obj_t}", e.pos, ErrorCode.E_INDEX_OBJECT_TYPE)
            return
        if isinstance(e, A.FString):
            for seg in e.segments:
                self._check_expr(seg, scope)
                t = A.expr_type(seg)
                if t not in (
                    "int",
                    "float",
                    "str",
                    "any",
                    "list",
                    "dict",
                    "tuple",
                    "set",
                ) and not t.startswith("instance:"):
                    raise SemaError(
                        f"f-string segment cannot be a {t}",
                        getattr(seg, "pos", e.pos),
                        ErrorCode.E_FSTRING_SEGMENT_TYPE,
                    )
            return
        if isinstance(e, A.Attr):
            # `<top-level func>.__code__.co_argcount`: a decorator-factory
            # pattern (`def deco(n): def wrap(f): table[n] = (f.__code__.co_argcount, f); ...`)
            # introspects a function's arity at module-init time to validate
            # call sites later. Functions have no runtime object wrapping
            # them here (just a bare code-pointer value), so this can't be a
            # real attribute read — fold it to the statically-known arity
            # instead. Only the exact 2-level `<Name>.__code__.co_argcount`
            # shape is recognized; anything else falls through to the normal
            # (lenient/opaque) attribute-access path below.
            if (
                e.name == "co_argcount"
                and isinstance(e.obj, A.Attr)
                and e.obj.name == "__code__"
                and isinstance(e.obj.obj, A.Name)
                and e.obj.obj.name in self.funcs
            ):
                e.inferred_type = "int"
                return
            # cls.field inside a @classmethod body → rewrite to ClassName.field,
            # UNLESS a subclass overrides that class var: then `cls.field` must
            # stay dynamic so an inherited classmethod dispatched on a subclass
            # reads the subclass's value (resolved at runtime by class id in
            # dynamic_classvar_compat_fixes), not the compiling owner's. The
            # static rewrite would otherwise bake in the base class's value for
            # every runtime subclass sharing the one compiled method symbol.
            if (
                isinstance(e.obj, A.Name)
                and self.classmethod_cls_param is not None
                and e.obj.name == self.classmethod_cls_param
                and self.current_class is not None
                and not self._class_var_overridden_in_subclass(
                    self.current_class, e.name
                )
            ):
                e.obj.name = self.current_class
            # A dynamic `cls.<classvar>` we deliberately left un-rewritten (a
            # subclass overrides it; lowering resolves the value per runtime
            # class id). It still has a KNOWN static SHAPE -- every override is
            # the same kind of class var -- so type the node (and stamp its
            # tuple/list element metadata) from `current_class`'s own
            # declaration, mirroring the `ClassName.x` class-var branch below.
            # Without this the read stays "any", and `realm in cls.realms`
            # takes _lower_membership's opaque path (`_lower_expr_inner`, which
            # bypasses the dynamic-classvar interception) instead of the
            # concrete tuple scan -- silently reading through a null `cls`.
            if (
                isinstance(e.obj, A.Name)
                and self.classmethod_cls_param is not None
                and e.obj.name == self.classmethod_cls_param
                and self.current_class is not None
            ):
                cvt_dyn = self._class_var_type(self.current_class, e.name)
                if cvt_dyn is not None:
                    e.inferred_type = cvt_dyn
                    cvcls_dyn: str = self.current_class
                    if cvt_dyn == "list":
                        e.list_el_type = self._resolve_field_el(cvcls_dyn, e.name)
                        if e.list_el_type in ("list", "dict"):
                            e.el_value_type = self._resolve_field_inner_value(cvcls_dyn, e.name)
                        elif e.list_el_type == "tuple":
                            e.el_tuple_types = self._resolve_field_value_tuple(cvcls_dyn, e.name)
                    elif cvt_dyn == "dict":
                        e.value_type = self._resolve_field_el(cvcls_dyn, e.name)
                        if e.value_type in ("list", "dict"):
                            e.inner_value_type = self._resolve_field_inner_value(cvcls_dyn, e.name)
                        elif e.value_type == "tuple":
                            e.value_tuple_elem_types = self._resolve_field_value_tuple(cvcls_dyn, e.name)
                    elif cvt_dyn == "tuple":
                        e.tuple_elem_types = self._resolve_field_tuple(cvcls_dyn, e.name)
                    return
            # `enum` extension: `Color.RED` resolves entirely at sema time --
            # fold this Attr node into an equivalent IntLit in place (mirrors
            # the Match -> If in-place rewrite elsewhere in this file), so
            # codegen/ir_lower need no EnumDecl-specific handling at all;
            # by the time either backend sees this node it's an ordinary int
            # constant. Cross-enum-type comparison is checked separately in
            # the A.Compare handler BEFORE this fold runs, since the fold
            # itself erases which enum (if any) a member came from.
            if isinstance(e.obj, A.Name) and e.obj.name in self.enum_types:
                members = self.enum_types[e.obj.name]
                if e.name not in members:
                    raise SemaError(
                        f"{e.obj.name} has no member {e.name!r}",
                        e.pos,
                        ErrorCode.E_ENUM_UNKNOWN_MEMBER,
                    )
                value = members[e.name]
                e.__class__ = A.IntLit  # type: ignore[assignment]
                e.value = value  # type: ignore[attr-defined]
                e.is_bool = False  # type: ignore[attr-defined]
                e.is_none = False  # type: ignore[attr-defined]
                return
            # Class-level variable read: `ClassName.x` (static constant). Type it
            # from the class var's default expression.
            if isinstance(e.obj, A.Name) and e.obj.name in self.classes:
                cvt = self._class_var_type(e.obj.name, e.name)
                if cvt is not None:
                    e.inferred_type = cvt
                    if self._field_is_bool(e.obj.name, e.name):
                        # `ClassName.flag` where `flag: bool` -- same rendering
                        # rule as the instance-qualified read below.
                        e.is_bool = True  # type: ignore[attr-defined]
                    # `ClassName.x` and `self.x` name the same storage, so this
                    # read carries the same collection element/value kinds the
                    # instance-qualified read below already carries. Without
                    # them a `ClassName.x[k]` / `.get(k)` result falls back to
                    # the "int" unknown sentinel and a str value gets formatted
                    # as a decimal integer.
                    cvcls: str = e.obj.name
                    if cvt == "list":
                        e.list_el_type = self._resolve_field_el(cvcls, e.name)
                        if e.list_el_type in ("list", "dict"):
                            e.el_value_type = self._resolve_field_inner_value(cvcls, e.name)
                        elif e.list_el_type == "tuple":
                            e.el_tuple_types = self._resolve_field_value_tuple(cvcls, e.name)
                    elif cvt == "dict":
                        e.value_type = self._resolve_field_el(cvcls, e.name)
                        if e.value_type in ("list", "dict"):
                            e.inner_value_type = self._resolve_field_inner_value(cvcls, e.name)
                        elif e.value_type == "tuple":
                            e.value_tuple_elem_types = self._resolve_field_value_tuple(cvcls, e.name)
                    elif cvt == "tuple":
                        e.tuple_elem_types = self._resolve_field_tuple(cvcls, e.name)
                    return
            # Special-case module attribute: math.pi, math.sqrt(...).
            if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
                bindings: dict = self.imported_modules[e.obj.name]
                if e.name not in bindings:
                    if e.obj.name == "os" and e.name == "environ":
                        # `os.environ` is conceptually a str->str dict. Typing
                        # it "dict" (not the opaque "any" every other unmodeled
                        # module attribute gets) routes `.copy()`,
                        # subscript-assign (`os.environ["X"] = v`), etc through
                        # the real dict codegen paths instead of the generic
                        # list-shaped IndexAssign fallback, which corrupts the
                        # stack when applied to a dict-shaped runtime value
                        # (confirmed via gdb on a selfhost rebuild: env =
                        # os.environ.copy(); env["PATH"] = ... miscompiled as
                        # a direct buffer index-write).
                        e.inferred_type = "dict"
                        e.value_type = "str"
                        return
                    # An attribute the curated registry doesn't model (e.g.
                    # `os.sep`). The real CPython module has it; stay lenient
                    # (opaque) rather than erroring, so source that uses
                    # unmodeled module attributes still type-checks.
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
            obj_t: str = A.expr_type(e.obj)
            if (
                e.name == "__name__"
                and isinstance(e.obj, A.Call)
                and e.obj.func == "type"
                and len(e.obj.args) == 1
            ):
                # `type(x).__name__` is always a str (the class's name).
                # ir_lower's `_lower_type_name_attr` already produces a real
                # string pointer here; without typing it "str", the result
                # read as "any"/int and (a) formatted as a raw pointer when
                # printed, and (b) hashed/compared wrong as a dict key or `in`
                # needle -- breaking the class-keyed-dict lowering (see
                # `_rewrite_class_keyed_dicts`) that rewrites `D[type(x)]` to
                # `D[type(x).__name__]`. Str typing routes it through
                # _runtime_str_eq / string hashing, the same path a literal
                # key uses, so the two match.
                e.inferred_type = "str"
                return
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
                        self._check_access(cls, e.name, is_field=False, pos=e.pos)
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
                            rt4: tuple = sig.ret_type  # type: ignore[misc]
                            ty: str = rt4[0]
                            el = rt4[1]
                            e.inferred_type = ty
                            if ty == "list" and el is not None:
                                e.list_el_type = el  # type: ignore[attr-defined]
                        else:
                            e.inferred_type = "int"
                        return
                    self._check_access(cls, e.name, is_field=True, pos=e.pos)
                    ft = self._resolve_field_type(cls, e.name)
                    e.inferred_type = ft if ft is not None else "any"
                    if self._field_is_bool(cls, e.name):
                        # A `bool`-annotated field renders True/False rather
                        # than 1/0; its static type stays "int" (bool IS int
                        # here), so nothing about the value changes.
                        e.is_bool = True  # type: ignore[attr-defined]
                    # Carry the collection element/value kinds so a later
                    # `self.xs[i]` / `for x in self.xs` reads the right kind.
                    if e.inferred_type == "list":
                        e.list_el_type = self._resolve_field_el(cls, e.name)
                        if e.list_el_type in ("list", "dict"):
                            e.el_value_type = self._resolve_field_inner_value(cls, e.name)
                        elif e.list_el_type == "tuple":
                            e.el_tuple_types = self._resolve_field_value_tuple(cls, e.name)
                    elif e.inferred_type == "dict":
                        e.value_type = self._resolve_field_el(cls, e.name)
                        if e.value_type in ("list", "dict"):
                            e.inner_value_type = self._resolve_field_inner_value(cls, e.name)
                        elif e.value_type == "tuple":
                            e.value_tuple_elem_types = self._resolve_field_value_tuple(cls, e.name)
                    elif e.inferred_type == "tuple":
                        e.tuple_elem_types = self._resolve_field_tuple(cls, e.name)
                else:
                    e.inferred_type = "any"
                return
            if obj_t == "module":
                # A merged stdlib module's own top-level value declarations
                # (e.g. `string.py`'s `ascii_lowercase: str = "..."`) are
                # hoisted into this module's `body` as plain `Assign`
                # statements by `program.py`'s whole-program merge (see
                # `_toplevel_value_assigns`), and checked -- registering
                # their real type into `self.global_scope` -- before any
                # statement referencing them via `module.NAME` can run
                # (source order). Use that real type when known, instead of
                # unconditionally collapsing to "any": an unqualified "any"
                # here previously made `len(module.NAME)` on a genuine `str`
                # constant route through the generic dict/list-length GEP
                # path instead of `strlen`, silently reading the wrong
                # field and returning garbage (confirmed via
                # `len(string.ascii_lowercase)` returning a huge garbage
                # int instead of 26). Falls back to "any" for anything
                # genuinely unmodeled (`sys.stderr`, an FFI module attr,
                # etc.), same as before.
                e.inferred_type = self.global_scope.types.get(e.name, "any")
                if e.inferred_type == "list":
                    e.list_el_type = self.global_scope.list_el_types.get(e.name, "int")
                elif e.inferred_type == "dict":
                    e.value_type = self.global_scope.dict_value_types.get(e.name, "int")
                return
            if obj_t == "any":
                # Attribute of an already-opaque value. Stay lenient.
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
            #
            # `(name, method) in INTERPRETER_ONLY_METHODS` is a tuple-in-
            # frozenset-of-tuples membership test. Codegen's set/dict
            # membership lowering (_gen_dict_in, "a set is a dict keyed by
            # its members") only supports str/int keys -- a tuple needle
            # falls through unconverted and gets used as a raw pointer
            # "key", which never correctly matches under self-compilation
            # (confirmed: this spuriously rejected `some_set.add(...)` for
            # EVERY set/dict .add()-like call under a selfhosted compiler,
            # since `("some_set_name", "add")` is never really a member but
            # the broken lowering doesn't reliably return False either).
            # `_is_interpreter_only_method` does the same check via a plain
            # tuple-of-string comparisons, avoiding tuple-keyed membership.
            if isinstance(e.obj, A.Name) and self._is_interpreter_only_method(
                e.obj.name, e.method
            ):
                raise SemaError(
                    f"{e.obj.name}.{e.method}() is not supported: dynamic import "
                    "requires a Python interpreter and cannot be compiled to "
                    "native code",
                    e.pos,
                    ErrorCode.E_INTERPRETER_ONLY_FEATURE,
                )
            # `ml.Code(config, source)` itself: _inject_mlang_if_needed
            # already ran (before this normal _check_block/_check_stmt
            # pass even starts) and stamped this exact node's
            # inferred_type to a `mlang:<uid>` marker directly. Without
            # this short-circuit, falling through to the generic
            # MethodCall dispatch below would re-typecheck `Code` as an
            # ordinary attribute lookup on the `ml` "module" (bound as a
            # plain opaque `scope.add(bind_name, "module")` dummy, since
            # `asmpython.mlang` isn't a real stdlib registry entry) and
            # silently overwrite the stamp back to "any" -- confirmed as
            # a real bug via a full IR dump: `code`'s type came out
            # "any" and `code.add(1, 2)` never reached this file's own
            # `mlang:` MethodCall case below at all, compiling to a
            # constant 0 with no call to `add` ever emitted.
            if isinstance(e.obj, A.Name) and e.method == "Code" and str(e.inferred_type).startswith("mlang:"):
                for a in e.args:
                    self._check_expr(a, scope)
                return
            # mlang Code(...) call: code.add(1, 2) where `code`'s static
            # type is a `mlang:<uid>` marker (stamped by
            # _inject_mlang_if_needed's Code(...)-assignment scan, mirrors
            # the `super:<Base>`/`instance:<Class>` marker-type pattern
            # already used elsewhere -- see that function's own docstring
            # for the full mechanism). The synthesized signature table
            # (self.mlang_code_funcs[uid]) was already built by shelling
            # out to the configured compiler during that same scan.
            self._check_expr(e.obj, scope)
            _obj_t_mlang = A.expr_type(e.obj)
            if _obj_t_mlang.startswith("mlang:"):
                uid = _obj_t_mlang.split(":", 1)[1]
                funcs = self.mlang_code_funcs.get(uid, {})
                if e.method not in funcs:
                    raise SemaError(
                        f"mlang Code(...) has no exported function {e.method!r} "
                        f"(known: {', '.join(sorted(funcs)) or '<none>'})",
                        e.pos,
                        ErrorCode.E_MLANG_UNKNOWN_EXPORT,
                    )
                sig = funcs[e.method]
                fn = stdlib.Func(arg_types=sig.arg_types, ret_type=sig.ret_type, c_name=e.method)
                self._check_ffi_call(fn, e.args, e.pos, scope, label=f"Code.{e.method}")
                e.inferred_type = sig.ret_type
                e._mlang_call = True  # type: ignore[attr-defined]
                return
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
                # os.cpu_count() -> int | None in real Python; asmpython has
                # no nullability tracking, so this always returns a positive
                # int (a worker-count fallback like `os.cpu_count() or 1`
                # never needs the None case to matter).
                if e.obj.name == "os" and e.method == "cpu_count":
                    e.inferred_type = "int"
                    return
                bindings: dict = self.imported_modules[e.obj.name]
                if e.method not in bindings or not isinstance(
                    bindings[e.method], stdlib.Func
                ):
                    raise SemaError(
                        f"module {e.obj.name!r} has no callable {e.method!r}",
                        e.pos,
                        ErrorCode.E_MODULE_NO_CALLABLE,
                    )
                fn = bindings[e.method]
                e.args = self._fold_variadic_ffi(fn, e, e.args)
                self._check_ffi_call(
                    fn, e.args, e.pos, scope, label=f"{e.obj.name}.{e.method}"
                )
                _fn_ret: str = getattr(fn, "ret_type", "int")
                e.inferred_type = _fn_ret if _fn_ret else "int"
                if getattr(fn, "ret_bool", False):
                    e.is_bool = True  # type: ignore[attr-defined]
                self._apply_ffi_element_return(fn, e, scope)
                return
            # `module.Thing(args)` where `module` is a merged *project* module
            # (not an FFI registry module) and `Thing` is a merged class or
            # top-level function (`stdlib.Func(...)`, `ospath.join(a, b)`):
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
                and (
                    # Only rewrite `mod.Thing()` -> `Thing()` when `mod` is a
                    # genuinely MERGED PROJECT module (its own classes/funcs
                    # were flattened into this program). An external CPython
                    # module used opaquely (e.g. `import ast; ast.MatchAs(...)`)
                    # whose leaf name merely collides with a merged user class
                    # must NOT be retargeted at that class's constructor --
                    # doing so enforced the user class's arity against the
                    # external call and rejected valid code (`ast.MatchAs(
                    # name=x)`, where ast's own MatchAs has an optional
                    # pattern). A function call (`ospath.join(...)`) has no such
                    # collision risk, so it stays permitted regardless, keeping
                    # the whole-program stdlib-function dispatch this rewrite
                    # was originally for.
                    e.method in self.funcs
                    or e.obj.name in getattr(self.mod, "project_module_qualifiers", set())
                )
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
            # `re.compile(...)` currently returns the pattern string itself,
            # but CPython-style code naturally calls regex APIs as methods on
            # that compiled object (`pat.finditer(text)`). Retarget those
            # method calls onto the merged stdlib functions so the native
            # compiler accepts the common compiled-regex surface.
            if obj_t == "str" and e.method == "finditer" and e.method in self.funcs:
                e.__class__ = A.Call  # type: ignore[assignment]
                e.func = e.method  # type: ignore[attr-defined]
                e.args = [e.obj] + e.args  # type: ignore[attr-defined]
                self._check_call(e, scope)  # type: ignore[arg-type]
                return
            if e.method == "indices":
                # slice.indices(length) -> (start, stop, step). The VM uses
                # this via `range(*index.indices(len(xs)))`; stamp the static
                # tuple shape so the existing `*tuple` expander can rewrite it.
                if len(e.args) != 1:
                    raise SemaError("slice.indices() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                if A.expr_type(e.args[0]) not in ("int", "any"):
                    raise SemaError("slice.indices() length must be an int", e.pos, ErrorCode.E_ARG_TYPE)
                e.inferred_type = "tuple"
                e.tuple_elem_types = ["int", "int", "int"]
                return
            if obj_t == "list":
                el_t = self._list_el_type(e.obj, scope)
                # Mark an append onto an EXPLICIT `list[object]` so ir_lower
                # boxes a scalar element (see `_explicit_object_lists`); a bare
                # `list` (also el "any", but element kind merely unknown) is
                # left raw so existing homogeneous-list code is unaffected.
                if (
                    e.method == "append"
                    and isinstance(e.obj, A.Name)
                    and e.obj.name in self._explicit_object_lists
                ):
                    e.box_element = True  # type: ignore[attr-defined]
                elif (
                    e.method == "append"
                    and isinstance(e.obj, A.Attr)
                    and el_t == "any"
                ):
                    # An instance FIELD typed `list[object]` (e.g.
                    # `self.items: list[object]`): its element kind resolves to
                    # the explicit "any" (a bare `list` field resolves to the
                    # "int" unknown-sentinel instead, never "any"), so an "any"
                    # element type here uniquely marks a genuinely heterogeneous
                    # object-list field. Box a scalar appended into it, exactly
                    # as the local-variable `list[object]` case above does, so a
                    # later `type(x)`/`isinstance(x, ...)` on the read-out element
                    # can still answer. This is the field analogue of
                    # `_explicit_object_lists`.
                    e.box_element = True  # type: ignore[attr-defined]
                if e.method == "append":
                    if len(e.args) != 1:
                        raise SemaError(
                            f"list.append() takes 1 argument, got {len(e.args)}",
                            e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    arg_t = A.expr_type(e.args[0])
                    if (
                        isinstance(e.obj, A.MethodCall)
                        and e.obj.method == "setdefault"
                        and isinstance(e.obj.obj, A.Name)
                        and arg_t not in ("int", "?")
                        and e.obj.obj.name not in self._explicit_object_dicts
                    ):
                        # `d.setdefault(k, []).append(v)` -- the appended value
                        # teaches the element kind of the lists this dict holds.
                        # The receiver is a temporary with no scope slot of its
                        # own, so record it against the DICT; otherwise a later
                        # `d[k]` read yields a list whose elements still read as
                        # raw ints (printing pointers instead of strings).
                        scope.dict_inner_value_types[e.obj.obj.name] = arg_t
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
                            ErrorCode.E_LIST_ELEMENT_TYPE_UNSUPPORTED,
                        )
                    if arg_t == "tuple" and isinstance(e.obj, A.Name):
                        # Appending a TUPLE teaches the element tuple's per-slot
                        # shape, not just "the elements are tuples". Without the
                        # shape the list's repr falls back to
                        # `_abi_list_repr`'s dict-items (str, int) assumption
                        # and formats a leading int as a string POINTER, so
                        # `out.append((1, 1)); print(out)` SEGFAULTED -- the
                        # ordinary way to build a list of pairs
                        # (`for x, y in zip(a, b): out.append((x, y))`).
                        # Appends that disagree on the shape widen each
                        # differing slot to "any", the same rule a list literal
                        # of tuples already uses.
                        _ap_slots = self._tuple_elem_types(e.args[0], scope)
                        if _ap_slots:
                            _prev_slots = scope.list_el_tuple_types.get(e.obj.name)
                            if not _prev_slots:
                                scope.list_el_tuple_types[e.obj.name] = list(_ap_slots)
                            elif len(_prev_slots) != len(_ap_slots):
                                scope.list_el_tuple_types[e.obj.name] = []
                            else:
                                _merged: list = []
                                for _sj in range(len(_ap_slots)):
                                    if _prev_slots[_sj] == _ap_slots[_sj]:
                                        _merged.append(_prev_slots[_sj])
                                    else:
                                        _merged.append("any")
                                scope.list_el_tuple_types[e.obj.name] = _merged
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
                    elif {el_t, arg_t} == {"bool", "int"}:
                        # `bool` is a subclass of `int` in Python, so a list may
                        # freely hold both (e.g. `[True, 0, False, 3]`). Neither
                        # is a genuine clash: widen the pinned element type to the
                        # common `int` so a later `int` append is also accepted
                        # (and an `int`-pinned list already accepts a `bool`,
                        # since `int` doubles as the accept-anything sentinel).
                        if el_t == "bool" and isinstance(e.obj, A.Name):
                            scope.list_el_types[e.obj.name] = "int"
                            e.obj.list_el_type = "int"
                            el_t = "int"
                    elif (
                        el_t not in ("any", "int")
                        and arg_t != el_t
                        and arg_t != f"instance:{el_t}"
                        and el_t
                        != (
                            arg_t.split(":", 1)[1]
                            if arg_t.startswith("instance:")
                            else ""
                        )
                        and not (
                            el_t.startswith("instance:")
                            and arg_t.startswith("instance:")
                            and (
                                el_t.split(":", 1)[1] not in self.classes
                                or self._class_descends_from(
                                    arg_t.split(":", 1)[1],
                                    el_t.split(":", 1)[1],
                                )
                            )
                        )
                    ):
                        # `int` doubles as the unknown element sentinel (e.g. a
                        # list produced by `list(<opaque>)` whose real element
                        # kind we never tracked), so don't reject a mismatch
                        # against it — only flag a genuine concrete clash.
                        raise SemaError(
                            f"list.append() expected {el_t}, got {arg_t}",
                            e.pos,
                            ErrorCode.E_ASSIGN_TYPE,
                        )
                    e.inferred_type = "int"  # returns None ~ 0
                elif e.method == "pop":
                    if len(e.args) > 1:
                        raise SemaError("list.pop() takes at most 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = el_t if el_t != "?" else "int"
                elif e.method == "extend":
                    # xs.extend(ys): append every element of another iterable.
                    # Python's extend takes any iterable; a tuple/set shares
                    # the list buffer layout at runtime so _abi_list_extend
                    # walks it identically to a list. str stays rejected (its
                    # elements are chars, a different element representation).
                    if len(e.args) != 1:
                        raise SemaError("list.extend() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                    at = A.expr_type(e.args[0])
                    if at not in ("list", "tuple", "set", "any"):
                        raise SemaError(
                            f"list.extend() expects an iterable, got {at}", e.pos,
                            ErrorCode.E_ARG_TYPE,
                        )
                    e.inferred_type = "int"  # returns None ~ 0
                elif e.method == "index":
                    # xs.index(v) -> position of the first matching element.
                    if len(e.args) != 1:
                        raise SemaError("list.index() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                elif e.method == "sort":
                    if e.args:
                        raise SemaError("list.sort() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    self._check_sort_kwargs(e, scope)
                    e.inferred_type = "int"  # in-place, returns None ~ 0
                elif e.method == "reverse":
                    if e.args:
                        raise SemaError("list.reverse() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                elif e.method == "count":
                    if len(e.args) != 1:
                        raise SemaError("list.count() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                elif e.method == "clear":
                    if e.args:
                        raise SemaError("list.clear() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                elif e.method == "copy":
                    if e.args:
                        raise SemaError("list.copy() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "list"
                    e.list_el_type = el_t if el_t not in ("?", "") else "int"
                elif e.method == "decode":
                    # bytes/bytearray are modeled as list[int]. decode([encoding])
                    # turns the byte list into a string; encoding/errors are
                    # accepted for CPython compatibility and ignored by the
                    # runtime helper (ASCII/UTF-8 byte-for-byte for now).
                    if len(e.args) > 2:
                        raise SemaError("bytes.decode() takes at most 2 arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    for a in e.args:
                        if A.expr_type(a) not in ("str", "any"):
                            raise SemaError("bytes.decode() arguments must be strings", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = "str"
                elif e.method == "ljust":
                    # list[int].ljust(width[, fill]) mirrors bytes.ljust and
                    # returns a padded byte list. The VM project uses this when
                    # constructing EDID descriptor payloads.
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError("bytes.ljust() takes width[, fill]", e.pos, ErrorCode.E_ARG_COUNT)
                    if A.expr_type(e.args[0]) not in ("int", "any"):
                        raise SemaError("bytes.ljust() width must be an int", e.pos, ErrorCode.E_ARG_TYPE)
                    if len(e.args) == 2 and A.expr_type(e.args[1]) not in ("list", "str", "any"):
                        raise SemaError("bytes.ljust() fill must be bytes or str", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = "list"
                    e.list_el_type = "int"
                elif e.method == "insert":
                    if len(e.args) != 2:
                        raise SemaError("list.insert() takes (index, value)", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                elif e.method == "remove":
                    if len(e.args) != 1:
                        raise SemaError("list.remove() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                else:
                    raise SemaError(f"list has no method {e.method!r}", e.pos, ErrorCode.E_NO_METHOD)
            elif obj_t == "dict":
                if e.method == "get":
                    # `d.get(k)` or `d.get(k, default)`. With one arg the default
                    # is the None-as-0 sentinel. Result is the dict's value kind
                    # so `cls = self.classes.get(k); cls.parent` resolves.
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError(
                            "dict.get() takes (key) or (key, default)", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    kt = A.expr_type(e.args[0])
                    if kt not in ("str", "any", "int") and not kt.startswith("instance:"):
                        raise SemaError("dict.get() key must be a str", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = self._dict_value_type(e.obj, scope)
                    # A dict with no tracked value kind (e.g. `d: dict = {}`,
                    # never populated with a literal at declaration time --
                    # `_dict_value_type` deliberately falls back to "any" for
                    # these, by design, so other dict operations stay
                    # lenient) leaves `_lower_expr_as_str` with NO way to
                    # runtime-dispatch how to print the result: this
                    # backend's dict/list slots carry no runtime type tag,
                    # only a compile-time-known "kind" byte baked in at
                    # lowering time, so an "any"-typed print() argument
                    # silently formats as a raw int regardless of the
                    # actual value (confirmed: d.get("k", "Hello") on a
                    # dict with no other type info printed "Hello"'s
                    # pointer as a decimal integer instead of the string).
                    # When there's a two-arg default with a KNOWN concrete
                    # type, that's the best available signal for what this
                    # call actually returns -- use it instead of "any".
                    # Doesn't help the one-arg `d.get(k)` form (no default
                    # to borrow a type from) or a genuinely heterogeneous
                    # dict (not supported by this compiler's model anyway;
                    # dict values are treated as homogeneously-typed
                    # elsewhere too, e.g. _dict_inner_value_type).
                    if e.inferred_type == "any" and len(e.args) == 2:
                        default_t = A.expr_type(e.args[1])
                        # Only borrow a FLOAT default's type: a float result
                        # keeps the `res_is_float` bitcast path in codegen (so
                        # at least the key-absent/default case is a real double),
                        # and an `any` dict's boxed float values are a known
                        # remaining gap either way. For an int/str default, leave
                        # the result "any": the values an `any` dict stores are
                        # BOXED, so codegen must unbox them (see the get() lower
                        # in ir_lower), and the box tag lets `print` format them
                        # -- which is what the old int/str override existed to
                        # work around before values were boxed.
                        if default_t == "float":
                            e.inferred_type = default_t
                    if e.inferred_type == "list":
                        e.list_el_type = self._dict_inner_value_type(e.obj, scope)
                        if e.list_el_type == "tuple":
                            e.tuple_elem_types = self._dict_value_tuple_types(e.obj, scope)
                    elif e.inferred_type == "dict":
                        e.value_type = self._dict_inner_value_type(e.obj, scope)
                    elif e.inferred_type == "tuple":
                        e.tuple_elem_types = self._dict_value_tuple_types(e.obj, scope)
                    if len(e.args) == 1:
                        e.dict_get_none_default = True
                elif e.method == "contains":
                    if len(e.args) != 1:
                        raise SemaError("dict.contains() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                    kt = A.expr_type(e.args[0])
                    if kt not in ("str", "any", "int") and not kt.startswith("instance:"):
                        raise SemaError("dict.contains() key must be a str", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = "int"
                elif e.method == "keys":
                    if e.args:
                        raise SemaError("dict.keys() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "list"
                    e.list_el_type = "str"
                elif e.method == "values":
                    if e.args:
                        raise SemaError("dict.values() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
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
                        raise SemaError("dict.items() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "list"
                    e.list_el_type = "tuple"
                    # Pair shape for `for k, v in d.items()` target typing.
                    e.tuple_elem_types = ["str", self._dict_value_type(e.obj, scope)]
                elif e.method == "update":
                    # d.update(other): merge another dict in. Lenient on the
                    # argument kind; returns None (~0).
                    if len(e.args) != 1:
                        raise SemaError("dict.update() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                elif e.method == "pop":
                    # d.pop(key[, default]) -> removes key, returns its value
                    # (or the default if absent). Returns the dict's value kind.
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError(
                            "dict.pop() takes (key) or (key, default)", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    if A.expr_type(e.args[0]) not in ("str", "any"):
                        raise SemaError("dict.pop() key must be a str", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = self._dict_value_type(e.obj, scope)
                    if e.inferred_type == "list":
                        e.list_el_type = self._dict_inner_value_type(e.obj, scope)
                        if e.list_el_type == "tuple":
                            e.tuple_elem_types = self._dict_value_tuple_types(e.obj, scope)
                    elif e.inferred_type == "dict":
                        e.value_type = self._dict_inner_value_type(e.obj, scope)
                    elif e.inferred_type == "tuple":
                        e.tuple_elem_types = self._dict_value_tuple_types(e.obj, scope)
                elif e.method == "clear":
                    if e.args:
                        raise SemaError("dict.clear() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "int"
                elif e.method == "popitem":
                    # d.popitem() -> removes and returns a (key, value) pair.
                    if e.args:
                        raise SemaError(
                            "dict.popitem() takes no arguments", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    e.inferred_type = "tuple"
                    e.tuple_elem_types = ["str", self._dict_value_type(e.obj, scope)]
                elif e.method == "copy":
                    if e.args:
                        raise SemaError("dict.copy() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "dict"
                    e.value_type = self._dict_value_type(e.obj, scope)
                    if e.value_type in ("list", "dict"):
                        e.inner_value_type = self._dict_inner_value_type(e.obj, scope)
                    elif e.value_type == "tuple":
                        e.value_tuple_elem_types = self._dict_value_tuple_types(e.obj, scope)
                elif e.method == "setdefault":
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError(
                            "dict.setdefault() takes (key[, default])", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    if A.expr_type(e.args[0]) not in ("str", "any"):
                        raise SemaError("dict.setdefault() key must be a str", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = self._dict_value_type(e.obj, scope)
                    if len(e.args) == 2 and e.inferred_type in ("int", "?"):
                        # The dict's value kind is unknown (a bare `{}` resolves
                        # to the "int" sentinel), but setdefault returns either
                        # the stored value or the DEFAULT -- so a concrete
                        # default tells us the kind. Without this,
                        # `d.setdefault(k, []).append(v)` -- the standard
                        # dict-of-lists idiom -- failed with "[E113] int has no
                        # method 'append'".
                        _sd_dt = A.expr_type(e.args[1])
                        if _sd_dt not in ("int", "?"):
                            e.inferred_type = _sd_dt
                            if (
                                isinstance(e.obj, A.Name)
                                and e.obj.name not in self._explicit_object_dicts
                            ):
                                # setdefault WRITES the default into the dict,
                                # so it teaches the dict's value kind exactly
                                # like a first `d[k] = v` does -- otherwise a
                                # later `d[k]` read still sees the unknown
                                # sentinel and returns raw bits.
                                scope.dict_value_types[e.obj.name] = _sd_dt
                            if _sd_dt == "list":
                                e.list_el_type = self._list_el_type(e.args[1], scope)
                                return
                            if _sd_dt == "dict":
                                e.value_type = self._dict_value_type(e.args[1], scope)
                                return
                    if e.inferred_type == "list":
                        e.list_el_type = self._dict_inner_value_type(e.obj, scope)
                        if e.list_el_type == "tuple":
                            e.tuple_elem_types = self._dict_value_tuple_types(e.obj, scope)
                    elif e.inferred_type == "dict":
                        e.value_type = self._dict_inner_value_type(e.obj, scope)
                    elif e.inferred_type == "tuple":
                        e.tuple_elem_types = self._dict_value_tuple_types(e.obj, scope)
                else:
                    raise SemaError(f"dict has no method {e.method!r}", e.pos, ErrorCode.E_NO_METHOD)
            elif obj_t == "str":
                self._check_str_method(e, scope)
                return
            elif obj_t == "int":
                if e.method in ("bit_length", "bit_count") and not e.args:
                    # bit_length(): position of the highest set bit.
                    # bit_count(): number of set bits (CPython 3.10+).
                    e.inferred_type = "int"
                    return
                if e.method == "to_bytes":
                    if not (1 <= len(e.args) <= 3):
                        raise SemaError("int.to_bytes() takes length[, byteorder[, signed]]", e.pos, ErrorCode.E_ARG_COUNT)
                    if A.expr_type(e.args[0]) not in ("int", "any"):
                        raise SemaError("int.to_bytes() length must be an int", e.pos, ErrorCode.E_ARG_TYPE)
                    if len(e.args) >= 2 and A.expr_type(e.args[1]) not in ("str", "any"):
                        raise SemaError("int.to_bytes() byteorder must be a str", e.pos, ErrorCode.E_ARG_TYPE)
                    if len(e.args) >= 3 and A.expr_type(e.args[2]) not in ("int", "any"):
                        raise SemaError("int.to_bytes() signed must be bool/int", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = "list"
                    e.list_el_type = "int"
                    return
                raise SemaError(f"int has no method {e.method!r}", e.pos, ErrorCode.E_NO_METHOD)
            elif (
                obj_t == "type"
                or (isinstance(e.obj, A.Name) and e.obj.name == "int")
            ):
                if (
                    isinstance(e.obj, A.Name)
                    and e.obj.name == "str"
                    and e.method == "maketrans"
                ):
                    # `str.maketrans(frm, to[, delete])` -- a translation table.
                    # asmpython models it as a dict mapping each single-char
                    # string to its replacement ("" for a deleted character),
                    # which is what `str.translate` consumes.
                    if not (2 <= len(e.args) <= 3):
                        raise SemaError(
                            "str.maketrans() takes (frm, to[, delete])", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    for _ma in e.args:
                        self._check_expr(_ma, scope)
                        if A.expr_type(_ma) not in ("str", "any"):
                            raise SemaError(
                                "str.maketrans() arguments must be str", e.pos,
                                ErrorCode.E_ARG_TYPE,
                            )
                    e.inferred_type = "dict"
                    e.value_type = "str"
                    return
                if (
                    isinstance(e.obj, A.Name)
                    and e.obj.name == "dict"
                    and e.method == "fromkeys"
                ):
                    # `dict.fromkeys(keys[, value])` -- a constructor on the
                    # type itself, so it lands here rather than in the dict
                    # method table.
                    if not (1 <= len(e.args) <= 2):
                        raise SemaError(
                            "dict.fromkeys() takes (keys[, value])", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    for _fa in e.args:
                        self._check_expr(_fa, scope)
                    if A.expr_type(e.args[0]) not in ("list", "tuple", "any"):
                        raise SemaError(
                            "dict.fromkeys() keys must be a list or tuple",
                            e.pos,
                            ErrorCode.E_ARG_TYPE,
                        )
                    e.inferred_type = "dict"
                    e.value_type = (
                        A.expr_type(e.args[1]) if len(e.args) == 2 else "int"
                    )
                    return
                if isinstance(e.obj, A.Name) and e.obj.name in self.classes:
                    cls_name = e.obj.name
                    resolved = self._resolve_method(cls_name, e.method)
                    if resolved is None:
                        raise SemaError(f"{cls_name} has no method {e.method!r}", e.pos, ErrorCode.E_NO_METHOD)
                    sig: FuncSig = resolved[1]
                    deco: list[str] = getattr(sig, "decorators", [])
                    if "classmethod" in deco:
                        expected = sig.arity - 1  # drop implicit cls
                    elif "staticmethod" in deco:
                        expected = sig.arity
                    else:
                        raise SemaError(
                            f"{cls_name}.{e.method}() needs an instance "
                            "(not a @staticmethod or @classmethod)",
                            e.pos,
                            ErrorCode.E_METHOD_NEEDS_INSTANCE,
                        )
                    required = expected - sig.n_defaults
                    if not (required <= len(e.args) <= expected):
                        raise SemaError(
                            f"{cls_name}.{e.method}() takes {required}..{expected} "
                            f"argument(s), got {len(e.args)}",
                            e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    if sig.ret_type is not None:
                        rt7: tuple = sig.ret_type  # type: ignore
                        ty: str = rt7[0]
                        el = rt7[1]
                        e.inferred_type = ty
                        if ty == "list" and el is not None:
                            e.list_el_type = el
                    else:
                        e.inferred_type = "int"
                    return
                if e.method == "from_bytes":
                    if not (1 <= len(e.args) <= 3):
                        raise SemaError("int.from_bytes() takes bytes[, byteorder[, signed]]", e.pos, ErrorCode.E_ARG_COUNT)
                    if A.expr_type(e.args[0]) not in ("list", "any"):
                        raise SemaError("int.from_bytes() first argument must be bytes/list[int]", e.pos, ErrorCode.E_ARG_TYPE)
                    if len(e.args) >= 2 and A.expr_type(e.args[1]) not in ("str", "any"):
                        raise SemaError("int.from_bytes() byteorder must be a str", e.pos, ErrorCode.E_ARG_TYPE)
                    if len(e.args) >= 3 and A.expr_type(e.args[2]) not in ("int", "any"):
                        raise SemaError("int.from_bytes() signed must be bool/int", e.pos, ErrorCode.E_ARG_TYPE)
                    e.inferred_type = "int"
                    return
                # A `@classmethod`/`@staticmethod` call on a `type` value whose
                # concrete class isn't a literal name (`obj_t == "type"` but
                # e.obj is a variable) -- e.g. iterating a tuple of classes:
                # `for root in (Server, Shared, Client): root.supports_runtime(r)`.
                # The class is unknown statically, so dispatch is deferred to
                # runtime (ir_lower emits an equality chain over every candidate
                # class id); here we only need to accept the call and infer its
                # return type. Every user class that resolves this method as a
                # class/static method is a candidate. If they agree on a return
                # type, use it; otherwise fall back to "any".
                if obj_t == "type":
                    candidates: list[FuncSig] = []
                    for _cname in self.classes:
                        _res = self._resolve_method(_cname, e.method)
                        if _res is None:
                            continue
                        _csig: FuncSig = _res[1]
                        _cdeco: list[str] = getattr(_csig, "decorators", [])
                        if "classmethod" in _cdeco or "staticmethod" in _cdeco:
                            candidates.append(_csig)
                    if candidates:
                        ret_kinds: list[str] = []
                        for _csig in candidates:
                            if _csig.ret_type is not None:
                                _rt: tuple = _csig.ret_type  # type: ignore
                                ret_kinds.append(_rt[0])
                            else:
                                ret_kinds.append("int")
                        if _all_same(ret_kinds):
                            e.inferred_type = ret_kinds[0]
                        else:
                            e.inferred_type = "any"
                        return
                raise SemaError(f"{obj_t} has no method {e.method!r}", e.pos, ErrorCode.E_NO_METHOD)
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
                    raise SemaError(f"{parent} has no method {e.method!r}", e.pos, ErrorCode.E_NO_METHOD)
                sig: FuncSig = resolved[1]
                expected = sig.arity - 1
                required = expected - sig.n_defaults
                if not (required <= len(e.args) <= expected):
                    raise SemaError(
                        f"super().{e.method}() takes {required}..{expected} "
                        f"argument(s), got {len(e.args)}",
                        e.pos,
                        ErrorCode.E_ARG_COUNT,
                    )
                if sig.ret_type is not None:
                    rt5: tuple = sig.ret_type  # type: ignore
                    ty: str = rt5[0]
                    el = rt5[1]
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el
                        if el in ("list", "dict") and sig.ret_inner_el_type:
                            e.el_value_type = sig.ret_inner_el_type
                else:
                    e.inferred_type = "int"
                return
            elif obj_t.startswith("instance:"):
                class_name = obj_t.split(":", 1)[1]
                ov_key = (class_name, e.method)
                if ov_key in self.method_overload_sets:
                    for a in e.args:
                        self._check_expr(a, scope)
                    ov_sig = self._resolve_overload(
                        f"{class_name}.{e.method}",
                        self.method_overload_sets[ov_key],
                        e.args,
                        e.pos,
                        implicit_self=True,
                    )
                    e.resolved_overload_symbol = _overload_symbol(e.method, ov_sig)
                    e.inferred_type = ov_sig.ret_type[0] if ov_sig.ret_type is not None else "int"
                    return
                resolved = self._resolve_method(class_name, e.method)
                if resolved is not None:
                    self._check_access(class_name, e.method, is_field=False, pos=e.pos)
                if resolved is None:
                    # `obj.field(args)` where `field` isn't a real method but
                    # IS an instance-typed field whose class has __call__
                    # (e.g. a Signal stored on an Instance: `part.touched`):
                    # rewrite into "read the field, call __call__ on it" --
                    # same `e.obj`/`e.method` shape the genuine instance-
                    # method dispatch path already handles, so no codegen
                    # changes are needed, just retargeting this node and
                    # recursing (mirrors the existing `e.__class__ = A.Call`
                    # retarget-and-recurse trick used a few lines above for
                    # `module.ClassName(...)` constructor calls). Guarded by
                    # `not e.via_field_call` so a __call__ that itself takes
                    # no further field-callable resolution doesn't loop.
                    cls_sig = self.classes.get(class_name)
                    field_ty = cls_sig.fields.get(e.method) if cls_sig is not None else None
                    if (
                        not getattr(e, "via_field_call", False)
                        and field_ty is not None
                        and field_ty.startswith("instance:")
                    ):
                        field_cls = field_ty.split(":", 1)[1]
                        if self._resolve_method(field_cls, "__call__") is not None:
                            e.obj = A.Attr(obj=e.obj, name=e.method, pos=e.pos)
                            e.method = "__call__"
                            e.via_field_call = True  # type: ignore[attr-defined]
                            self._check_expr(e, scope)
                            return
                    if class_name not in self.classes or self._has_external_base(
                        class_name
                    ):
                        # Either the receiver is an external/imported instance
                        # we don't model at all (e.g. an `argparse.ArgumentParser`
                        # bound to a typed param), or the method lives on an
                        # unmodeled external base (a subclass of an imported
                        # Codegen calling self.emit). Accept it; result is an
                        # opaque value so chained calls stay lenient. Args
                        # still need their own type-checking pass (sema-level
                        # side effects elsewhere may depend on every arg
                        # having been visited) even though this call's own
                        # signature is unknown -- previously this returned
                        # before reaching the `for a in e.args` check below,
                        # leaving an inherited method call's arguments
                        # entirely unchecked.
                        for _ext_a in e.args:
                            self._check_expr(_ext_a, scope)
                        e.inferred_type = "any"
                        return
                    if self._try_field_callable_call(e, class_name, scope):
                        return
                    raise SemaError(
                        f"{class_name} has no method {e.method!r}",
                        e.pos,
                        ErrorCode.E_NO_METHOD,
                    )
                sig: FuncSig = resolved[1]
                # Method arity counts self; user passed args don't include self.
                # @staticmethod has no implicit self so don't subtract 1.
                sig_decorators: list[str] = getattr(sig, "decorators", [])
                is_static_m = "staticmethod" in sig_decorators
                expected = sig.arity if is_static_m else sig.arity - 1
                required = expected - sig.n_defaults
                if sig.vararg is None and sig.kwarg is None and not e.kwargs:
                    if not (required <= len(e.args) <= expected):
                        raise SemaError(
                            f"{class_name}.{e.method}() takes {required}..{expected} argument(s), got {len(e.args)}",
                            e.pos,
                            ErrorCode.E_ARG_COUNT,
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
                    rt6: tuple = sig.ret_type  # type: ignore
                    ty: str = rt6[0]
                    el = rt6[1]
                    e.inferred_type = ty
                    if sig.ret_bool:
                        e.is_bool = True
                    if ty == "list" and el is not None:
                        e.list_el_type = el
                        if el == "tuple" and sig.ret_list_tuple_types:
                            e.tuple_elem_types = list(sig.ret_list_tuple_types)  # type: ignore
                        elif el in ("list", "dict") and sig.ret_inner_el_type:
                            e.el_value_type = sig.ret_inner_el_type
                elif sig.returns_self:
                    # `def m(self): ... return self` with no annotation: the
                    # call's result is another reference to the receiver's
                    # type (e.g. `__enter__` returning `self`).
                    e.inferred_type = obj_t
                else:
                    e.inferred_type = "int"
            elif obj_t == "module" and e.method in self.funcs:
                # A module-qualified call to a merged project function
                # (`ospath.join(a, b)`, etc.): adopt the
                # function's signature so the result is typed like a plain call
                # (codegen dispatches it to the merged symbol).
                msig = self.funcs[e.method]
                if msig.ret_tuple is not None:
                    e.inferred_type = "tuple"
                    e.tuple_elem_types = list(msig.ret_tuple)  # type: ignore
                elif msig.ret_type is not None:
                    # Same fix as _check_call's plain-call path: explicit
                    # subscript reads instead of a tuple-unpack of
                    # msig.ret_type (declared `ret_type: object` on FuncSig,
                    # not a concrete type sema can track field-wise). This is
                    # the exact path `module_name.func_name(...)` calls go
                    # through for a plain `import X` (not `from X import Y`)
                    # -- left uncorrupted callers (a working `from X import Y`)
                    # unaffected while every `import X` + `X.func()` call
                    # site got the wrong inferred_type, which cascaded into
                    # severe miscompilation (observed as a hard crash with a
                    # corrupted instruction pointer) for any program -- like
                    # asmpython's own __main__.py -- using plain `import X`.
                    mret_tuple_val: tuple = msig.ret_type  # type: ignore
                    mty: str = mret_tuple_val[0]
                    mel = mret_tuple_val[1]
                    mval = mret_tuple_val[2]
                    e.inferred_type = mty
                    if mty == "list" and mel is not None:
                        e.list_el_type = mel
                        if mel in ("list", "dict") and msig.ret_inner_el_type:
                            e.el_value_type = msig.ret_inner_el_type
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
                    # Explicit `: str` read: A.expr_type(...) is a plain
                    # function call (not a method), and its result landing
                    # in an unannotated local read back "any" instead of
                    # "str" -- so `arg_t not in ("str", "int", "any")` always
                    # compared a real string pointer against the tuple's
                    # literal pointers without going through _runtime_str_eq,
                    # never matching any of them and raising this SemaError
                    # for EVERY set.add()/discard()/remove() call regardless
                    # of the real argument type. This made `set().add(x)`
                    # uncompilable in any selfhosted binary, including
                    # program.py's own func_names/class_names dedup sets
                    # used during whole-program merge -- almost certainly the
                    # root cause behind self.mod.funcs ending up empty when
                    # self-compiling the full multi-file compiler source.
                    arg_t: str = A.expr_type(e.args[0])
                    if arg_t not in ("str", "int", "any"):
                        raise SemaError(
                            f"set.{e.method}({arg_t}) is not supported yet "
                            "(sets are str/int-keyed in v1)",
                            e.args[0].pos,
                            ErrorCode.E_SET_KEY_TYPE,
                        )
                    e.inferred_type = "int"
                elif e.method in ("update", "clear"):
                    e.inferred_type = "int"
                elif e.method in (
                    "isdisjoint", "issuperset",
                    "intersection_update", "difference_update",
                ):
                    # The testing / in-place set operations. The two
                    # predicates render True/False (see is_bool_expr); the two
                    # *_update forms mutate and return None.
                    if len(e.args) != 1:
                        raise SemaError(
                            f"set.{e.method}() takes 1 argument", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    if A.expr_type(e.args[0]) not in ("set", "any"):
                        raise SemaError(
                            f"set.{e.method}() argument must be a set, "
                            f"got {A.expr_type(e.args[0])}",
                            e.pos,
                            ErrorCode.E_ARG_TYPE,
                        )
                    e.inferred_type = "int"
                elif e.method in ("union", "intersection", "difference"):
                    if len(e.args) != 1:
                        raise SemaError(
                            f"set.{e.method}() takes 1 argument", e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    e.inferred_type = "set"
                elif e.method == "copy":
                    if e.args:
                        raise SemaError("set.copy() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
                    e.inferred_type = "set"
                elif e.method == "pop":
                    if e.args:
                        raise SemaError("set.pop() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
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
                    if self._try_field_callable_call(e, cls_name, scope):
                        return
                    raise SemaError(
                        f"{cls_name} has no method {e.method!r}", e.pos,
                        ErrorCode.E_NO_METHOD,
                    )
                sig: FuncSig = resolved[1]
                deco: list[str] = getattr(sig, "decorators", [])
                if "classmethod" in deco:
                    expected = sig.arity - 1  # drop implicit cls
                elif "staticmethod" in deco:
                    expected = sig.arity
                else:
                    raise SemaError(
                        f"{cls_name}.{e.method}() needs an instance "
                        "(not a @staticmethod or @classmethod)",
                        e.pos,
                        ErrorCode.E_METHOD_NEEDS_INSTANCE,
                    )
                required = expected - sig.n_defaults
                if not (required <= len(e.args) <= expected):
                    raise SemaError(
                        f"{cls_name}.{e.method}() takes {required}..{expected} "
                        f"argument(s), got {len(e.args)}",
                        e.pos,
                        ErrorCode.E_ARG_COUNT,
                    )
                for a in e.args:
                    self._check_expr(a, scope)
                if sig.ret_type is not None:
                    rt7: tuple = sig.ret_type  # type: ignore
                    ty: str = rt7[0]
                    el = rt7[1]
                    e.inferred_type = ty
                    if ty == "list" and el is not None:
                        e.list_el_type = el
                else:
                    e.inferred_type = "int"
            else:
                raise SemaError(f"{obj_t} has no method {e.method!r}", e.pos, ErrorCode.E_NO_METHOD)
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
            # A lambda parameter is normally opaque ("any") -- nothing at the
            # definition site says what it will be called with. But a lambda
            # passed to sorted(key=)/min/max/map/filter is ALWAYS called with
            # the source sequence's elements, and those call sites stamp
            # `param_hint` with that element kind. Using it matters: with "any",
            # `len(w)` on a str element lowers to a LIST-header read (a real str
            # has no header) and yields garbage, and `w.upper()` doesn't resolve
            # at all. Only the FIRST parameter is hinted (the element).
            _p_hint = getattr(e, "param_hint", None)
            _p_slots = list(getattr(e, "param_tuple_types", []) or [])
            for _pi, p in enumerate(e.params):
                if _pi == 0 and _p_hint == "tuple" and _p_slots:
                    # A tuple parameter with known slot kinds, so `p[1]` inside
                    # the body resolves to that slot's type rather than "any".
                    inner_scope.add(p, "tuple", tuple_types=_p_slots)
                else:
                    inner_scope.add(p, _p_hint if (_pi == 0 and _p_hint) else "any")
            if e.vararg:
                # Vararg absorbs surplus positional args into a list at the
                # call site (identical binding as a real `def f(*args)`, see
                # _ensure_synthetic_func's vararg passthrough below) -- typed
                # "any" like every other lambda parameter, since a lambda's
                # inner scope has no annotation to type it more precisely.
                inner_scope.add(e.vararg, "any")
            ret_t = "int"
            if e.body is not None:
                try:
                    self._check_expr(e.body, inner_scope)
                    ret_t = A.expr_type(e.body)
                except Exception:
                    pass
            # A real `def`'s own vararg name is included in its `params`
            # list (see _parse_funcdef) -- match that convention exactly so
            # arity/param_names line up the same way for every other
            # FuncSig consumer (call-site binding, codegen's argcount
            # correction, etc.), rather than inventing a lambda-specific
            # shape those consumers would need a special case for.
            synth_params = list(e.params) + ([e.vararg] if e.vararg else [])
            synth_param_types = [None] * len(e.params) + (
                [("list", None)] if e.vararg else []
            )
            if _p_hint and e.params:
                # Carry the element-kind hint into the synthesized function's
                # own signature too, so its BODY compiles against the real kind
                # (the inner_scope typing above only affects this check pass).
                synth_param_types[0] = (_p_hint, None)
            self._ensure_synthetic_func(
                A.FuncDef(
                    name=lname,
                    params=synth_params,
                    body=[A.Return(value=e.body, pos=e.pos)],
                    pos=e.pos,
                    param_types=synth_param_types,
                    vararg=e.vararg,
                ),
                ret_t,
            )
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
            f"internal: unhandled expr {type(e).__name__}", getattr(e, "pos", None),
            ErrorCode.E_INTERNAL_UNHANDLED_NODE,
        )

    # Intentionally NOT stored as a class variable with nested tuples.
    # When self-compiled, `self.STR_METHODS.get(name)` returns "any"-typed
    # and `len(arg_types)` on an opaque tuple calls _emit_strlen on the list
    # header (reading the capacity field as a C string), giving a garbage
    # count (always 1) instead of the real arg count.  Two flat int/str dicts
    # at module level avoid both the opaque dict-get issue and the bad len().
    # See _check_str_method for how they're used.

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
                "'%' string formatting requires a literal format string", e.pos,
                ErrorCode.E_FORMAT_LITERAL,
            )
        try:
            pieces, nconv = A.parse_pct_format(e.left.value)
        except ValueError as exc:
            raise SemaError(f"bad format string: {exc}", e.pos, ErrorCode.E_BAD_FORMAT_STRING)
        args = e.right.elems if isinstance(e.right, A.TupleLit) else [e.right]
        if len(args) != nconv:
            raise SemaError(
                f"'%' format string expects {nconv} argument(s), got {len(args)}",
                e.pos,
                ErrorCode.E_FORMAT_ARG_COUNT,
            )
        ai = 0
        for piece in pieces:
            if piece[0] != "arg":
                continue
            conv = piece[4]
            t = A.expr_type(args[ai])
            if conv in "dioxX" and t not in ("int", "any"):
                raise SemaError(f"'%{conv}' format requires an int argument", e.pos, ErrorCode.E_FORMAT_ARG_TYPE)
            if conv in "eEfFgG" and t not in ("int", "float", "any"):
                raise SemaError(f"'%{conv}' format requires a numeric argument", e.pos, ErrorCode.E_FORMAT_ARG_TYPE)
            ai += 1
        e.inferred_type = "str"  # type: ignore

    def _callable_name_as_lambda(
        self, name: str, scope: Scope, pos, param_hint: "Optional[str]" = None,
        param_tuple_types: "Optional[list]" = None,
    ) -> "Optional[A.Lambda]":
        """Wrap a bare one-argument callable NAME in the equivalent lambda
        (`len` -> `lambda _k: len(_k)`), or None if the name isn't one.

        The `key=`/`map()`/`filter()` lowerings all call a Lambda's synthesized
        function, so a bare name reference -- a builtin, a top-level function,
        or a variable bound to a lambda -- has nothing to dispatch to and used
        to be a hard error. Checking the synthesized Lambda here creates that
        hidden function (see `_check_expr`'s A.Lambda case), producing exactly
        the shape a hand-written `lambda x: len(x)` compiles to, so no new
        lowering path is needed.
        """
        is_callable_name = (
            name in self.lambda_rets
            or name in self.funcs
            or (name in BUILTINS and BUILTINS[name][0] <= 1 <= BUILTINS[name][1])
        )
        if not is_callable_name:
            return None
        lam = A.Lambda(
            params=["_k"],
            body=A.Call(func=name, args=[A.Name(name="_k", pos=pos)], pos=pos),
            pos=pos,
        )
        if param_hint:
            lam.param_hint = param_hint  # type: ignore[attr-defined]
            if param_tuple_types:
                lam.param_tuple_types = list(param_tuple_types)  # type: ignore[attr-defined]
        self._check_expr(lam, scope)
        return lam

    # Builtins whose argument is consumed as a SEQUENCE. An iterable object
    # (a generator's result, a deque, any class with the iterator or sequence
    # protocol) is a perfectly good argument to all of them in Python, so rather
    # than teach each one about instances -- and get a different answer per
    # builtin -- they share one coercion: drain the object with `list(...)`
    # first. `list()` itself already knows both protocols.
    # Only `enumerate` for now, and only because its for-position argument is
    # evaluated as a statement. The rest of the family -- sum/sorted/max/min/
    # any/all/tuple/set/reversed/zip/map/filter, all of which want the same
    # coercion -- was held back on a lowering hazard, NOT on anything about
    # them: `list(<iterable object>)` drains through a setjmp loop, and that
    # loop miscompiled when emitted inside another expression's evaluation
    # (`xs = list(gen()); sum(xs)` was correct while `sum(list(gen()))`
    # segfaulted). The backend's block-layout fix cleared that, so the full set
    # is enabled. Each entry is the argument POSITIONS that take a sequence.
    _SEQUENCE_BUILTIN_ARGS: dict = {
        "enumerate": (0,),
        "sum": (0,),
        "sorted": (0,),
        "min": (0,),
        "max": (0,),
        "any": (0,),
        "all": (0,),
        "tuple": (0,),
        "set": (0,),
        # `reversed` is here for its STR argument only -- `reversed('abc')` is
        # valid Python. The instance branch below refuses it, because CPython
        # raises "'generator' object is not reversible" and accepting one would
        # make asmpython take a program CPython rejects.
        "reversed": (0,),
        "zip": (0, 1, 2),
        "map": (1,),
        "filter": (1,),
    }

    def _coerce_iterable_instance_args(self, e: A.Call, scope: Scope) -> None:
        """Wrap an iterable-object argument in `list(...)` for the builtins that
        consume a sequence, so every one of them accepts a generator result or a
        custom iterator without its own special case."""
        positions = self._SEQUENCE_BUILTIN_ARGS.get(e.func)
        if not positions:
            return
        if (
            e.func in self.funcs
            or e.func in self.classes
            or e.func in self.mod.func_aliases
        ):
            # Builtins are SHADOWABLE. `from fnmatch import fnfilter as filter`
            # binds a two-argument (names, pattern) function to the name
            # `filter`, whose second argument is a PATTERN STRING -- coercing
            # it as if it were builtin filter's sequence argument drained the
            # pattern into a list of characters. Argument coercion keyed on a
            # builtin's name must never apply to a name the program has rebound.
            return
        for i in positions:
            if i >= len(e.args):
                continue
            arg = e.args[i]
            if isinstance(arg, A.Call) and arg.func == "list":
                continue  # already drained
            if isinstance(arg, (A.Name, A.Attr, A.Subscript)):
                # Same reason as the Call case below: the coercion decides from
                # the argument's TYPE, and none of these carry a real one until
                # `_check_expr` stamps it -- a bare `scores` read "int" and a
                # dict argument slipped through untouched. All three are
                # idempotent to check (unlike a Call, which re-runs
                # `_bind_args`), so there is nothing to guard against here.
                self._check_expr(arg, scope)
            if isinstance(arg, A.Call) and not getattr(arg, "_seq_precheck", False):
                # The argument's TYPE is what decides whether it needs draining,
                # and a Call node carries its default "int" until it has been
                # checked -- this coercion runs before the builtin's own branch
                # checks its arguments, so an unchecked `gen()` looked like a
                # plain int and every generator argument slipped through
                # untouched (the sequence builtin then read the iterator OBJECT
                # as a list header and faulted).
                #
                # A call to a `*args`/`**kwargs` callee is left alone: checking
                # it twice would re-run `_bind_args` over an already-packed
                # argument list and nest the packed list inside itself.
                _cal = self.funcs.get(arg.func)
                if _cal is None or (
                    getattr(_cal, "vararg", None) is None
                    and getattr(_cal, "kwarg", None) is None
                ):
                    arg._seq_precheck = True  # type: ignore[attr-defined]
                    self._check_expr(arg, scope)
            t = A.expr_type(arg)
            if t in ("dict", "set") and e.func in ("min", "max"):
                # Iterating a dict yields its KEYS (a set, its members), but
                # min/max read their argument as a list header, so the mapping
                # object went through as one and faulted. `list(d)` is already
                # exactly "the keys as a list" -- the same coercion, one more
                # source kind. Only min/max need it: `sorted`/`sum`/`any`/`all`
                # over a mapping already route through their own key walk.
                if len(e.args) != 1:
                    continue
                drained_d = A.Call(func="list", args=[arg], kwargs=[], pos=arg.pos)
                self._check_expr(drained_d, scope)
                e.args[i] = drained_d
                continue
            if t == "str":
                # A str IS a sequence in CPython -- `sorted('listen')` gives
                # its characters, `min('banana')` its smallest one. These
                # lowerings all read their argument as a LIST HEADER, so a raw
                # char pointer went straight through as one and faulted.
                # Draining to `list(s)` (a lowering that already exists) is the
                # same coercion an iterable object gets, just from a different
                # source kind.
                #
                # `sum` is excluded: CPython raises TypeError for `sum('abc')`,
                # so accepting it would be a divergence, not a fix.
                if e.func == "sum":
                    continue
                if e.func in ("min", "max") and len(e.args) != 1:
                    continue  # the 2-arg form compares its arguments directly
                drained_s = A.Call(func="list", args=[arg], kwargs=[], pos=arg.pos)
                self._check_expr(drained_s, scope)
                e.args[i] = drained_s
                continue
            if not t.startswith("instance:"):
                continue
            if e.func == "reversed":
                continue  # see the table: a generator is not reversible
            if e.func in ("min", "max") and len(e.args) != 1:
                continue  # ditto: `max(a, b)` compares, it does not iterate
            cls = t.split(":", 1)[1]
            if (
                self._resolve_method(cls, "__next__") is None
                and self._resolve_method(cls, "__getitem__") is None
            ):
                continue  # not iterable; leave it to the normal arg check
            drained = A.Call(func="list", args=[arg], kwargs=[], pos=arg.pos)
            self._check_expr(drained, scope)
            e.args[i] = drained

    def _check_sort_kwargs(self, e, scope: Scope, allow_default: bool = False) -> None:
        """Validate and resolve the `key=`/`reverse=` kwargs shared by
        `sorted()`, `min()`/`max()`, and `list.sort()`.

        `key=<lambda literal>`, `key=<name bound to a lambda>`, and
        `key=<bare top-level function reference>` are all supported — each
        resolves to a function-pointer value loaded via the same indirect-call
        convention (`_emit_sort_keys_list` does `mov rax, [fn]; call rax`).
        Stamps `e.sort_key` (Optional[expr]), `e.sort_key_ret` ("str"/"int"),
        and `e.sort_reverse` (Optional[expr]), then clears `e.kwargs` so
        normal call-arg checks don't see them.

        `allow_default` additionally accepts min()/max()'s `default=` kwarg
        (the value returned when the iterable is empty); it stamps
        `e.minmax_default` (Optional[expr]). Only min/max pass it.
        """
        key_expr = None
        reverse_expr = None
        default_expr = None
        for kname, kexpr in e.kwargs:
            if kname == "key":
                key_expr = kexpr
            elif kname == "reverse":
                reverse_expr = kexpr
            elif kname == "default" and allow_default:
                default_expr = kexpr
            else:
                raise SemaError(f"unexpected keyword argument {kname!r}", e.pos, ErrorCode.E_ARG_COUNT)
        if key_expr is not None:
            # The key function is always called with the sequence's ELEMENTS,
            # so hint the lambda's parameter with that kind (see `param_hint`
            # in _check_expr's A.Lambda case) -- without it a str element's
            # `len(s)`/`s.lower()` inside the key mistypes. The sequence is the
            # receiver for `list.sort()` and the first argument otherwise; both
            # have already been checked by the time this runs.
            _seq = e.obj if isinstance(e, A.MethodCall) else (e.args[0] if e.args else None)
            _seq_el = self._iter_element_type(_seq, scope) if _seq is not None else None
            if _seq_el in ("", "?", "any"):
                _seq_el = None
            # For a sequence of TUPLES, also carry the per-slot kinds, so a
            # `lambda p: p[1]` key body resolves to that slot's real type
            # instead of "any". That matters beyond neatness: an "any" key was
            # loaded as a raw 8-byte value and sorted with the INTEGER
            # comparator, so a str key sorted by pointer address -- silently
            # returning an unsorted list.
            _seq_slots = (
                self._list_el_tuple_types(_seq, scope)
                if (_seq is not None and _seq_el == "tuple")
                else []
            )
            if _seq_el and isinstance(key_expr, A.Lambda):
                key_expr.param_hint = _seq_el  # type: ignore[attr-defined]
                if _seq_slots:
                    key_expr.param_tuple_types = list(_seq_slots)  # type: ignore[attr-defined]
            self._check_expr(key_expr, scope)
            if isinstance(key_expr, A.Lambda):
                ret_t: str = getattr(key_expr, "lambda_ret", "int")
            elif isinstance(key_expr, A.Name):
                # A bare callable NAME -- a top-level function, a variable
                # bound to a lambda, or a one-argument builtin (`key=len`).
                # All three become the equivalent lambda: the sort lowering
                # only knows how to call a Lambda's synthesized function, so a
                # Name reached it as "unsupported expr Call (sorted key)" (or,
                # for a builtin, was rejected outright here).
                _kname = key_expr.name
                if _kname in self.funcs and _kname not in self.lambda_rets:
                    _ksig = self.funcs[_kname]
                    if _ksig.arity - _ksig.n_defaults > 1:
                        raise SemaError(
                            f"key= function {_kname!r} must take exactly "
                            "one argument (the element being compared)",
                            e.pos,
                            ErrorCode.E_SORT_KEY_ARITY,
                        )
                _klam = self._callable_name_as_lambda(
                    _kname, scope, e.pos, param_hint=_seq_el,
                    param_tuple_types=_seq_slots,
                )
                if _klam is None:
                    raise SemaError(
                        "key= must be a lambda literal, a name bound to a "
                        f"lambda, or a top-level function ({_kname!r} "
                        "is none of these)",
                        e.pos,
                        ErrorCode.E_SORT_KEY_TYPE,
                    )
                key_expr = _klam
                ret_t = getattr(_klam, "lambda_ret", "int")
            else:
                # The remaining conventional key= spellings are all shorthand
                # for a one-argument lambda, so rewrite them into one and let
                # the ordinary lambda path handle the rest:
                #   str.lower       -> lambda _k: _k.lower()   (unbound method)
                #   itemgetter(N)   -> lambda _k: _k[N]
                #   attrgetter("a") -> lambda _k: _k.a
                _kbody = None
                if (
                    isinstance(key_expr, A.Attr)
                    and isinstance(key_expr.obj, A.Name)
                    and (
                        key_expr.obj.name in BUILTIN_TYPE_NAMES
                        or key_expr.obj.name in self.classes
                    )
                ):
                    _kbody = A.MethodCall(
                        obj=A.Name(name="_k", pos=e.pos),
                        method=key_expr.name,
                        args=[],
                        kwargs=[],
                        pos=e.pos,
                    )
                elif (
                    isinstance(key_expr, A.Call)
                    and key_expr.func == "itemgetter"
                    and len(key_expr.args) == 1
                ):
                    _kbody = A.Subscript(
                        obj=A.Name(name="_k", pos=e.pos),
                        index=key_expr.args[0],
                        pos=e.pos,
                    )
                elif (
                    isinstance(key_expr, A.Call)
                    and key_expr.func == "attrgetter"
                    and len(key_expr.args) == 1
                    and isinstance(key_expr.args[0], A.StrLit)
                ):
                    _kbody = A.Attr(
                        obj=A.Name(name="_k", pos=e.pos),
                        name=key_expr.args[0].value,
                        pos=e.pos,
                    )
                if _kbody is None:
                    raise SemaError(
                        "key= must be a lambda literal, a name bound to a "
                        "lambda, a one-argument builtin/function, an unbound "
                        "method like str.lower, or itemgetter/attrgetter",
                        e.pos,
                        ErrorCode.E_SORT_KEY_TYPE,
                    )
                key_expr = A.Lambda(params=["_k"], body=_kbody, pos=e.pos)
                if _seq_el:
                    key_expr.param_hint = _seq_el  # type: ignore[attr-defined]
                    if _seq_slots:
                        key_expr.param_tuple_types = list(_seq_slots)  # type: ignore[attr-defined]
                self._check_expr(key_expr, scope)
                ret_t = getattr(key_expr, "lambda_ret", "int")
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
        if default_expr is not None:
            self._check_expr(default_expr, scope)
        e.minmax_default = default_expr  # type: ignore[attr-defined]
        e.kwargs = []

    def _check_str_method(self, e: A.MethodCall, scope: Scope) -> None:
        # Methods with non-trivial signatures: split returns list[str]; join
        # consumes a list[str].
        if e.method == "split":
            # str.split([sep[, maxsplit]]). asmpython accepts the optional
            # maxsplit int (front-end); codegen currently ignores it and splits
            # on all occurrences (a full maxsplit lowering is a runtime TODO).
            if len(e.args) > 2:
                raise SemaError("str.split() takes 0 to 2 arguments", e.pos, ErrorCode.E_ARG_COUNT)
            if (
                e.args
                and A.expr_type(e.args[0]) not in ("str", "any")
                and not A.is_none_expr(e.args[0])
            ):
                raise SemaError("str.split() separator must be str", e.pos, ErrorCode.E_ARG_TYPE)
            if len(e.args) == 2 and A.expr_type(e.args[1]) not in ("int", "any"):
                raise SemaError("str.split() maxsplit must be an int", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "list"
            e.list_el_type = "str"
            return
        if e.method == "splitlines":
            # Optional `keepends` bool arg is accepted and ignored.
            if len(e.args) > 1:
                raise SemaError("str.splitlines() takes 0 or 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
            e.inferred_type = "list"
            e.list_el_type = "str"
            return
        if e.method == "rsplit":
            # str.rsplit(sep, 1): split at the LAST occurrence of sep ->
            # [before, after] (or [s] when absent). Only the maxsplit=1 form is
            # lowered today; other counts need a general right-scan runtime.
            if len(e.args) != 2:
                raise SemaError(
                    "str.rsplit() currently requires exactly (sep, 1)", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            if A.expr_type(e.args[0]) not in ("str", "any"):
                raise SemaError("str.rsplit() separator must be str", e.pos, ErrorCode.E_ARG_TYPE)
            if not (isinstance(e.args[1], A.IntLit) and e.args[1].value == 1):
                raise SemaError(
                    "str.rsplit() maxsplit must be the literal 1 (only the "
                    "last-separator split is implemented)",
                    e.pos,
                    ErrorCode.E_RSPLIT_MAXSPLIT,
                )
            e.inferred_type = "list"
            e.list_el_type = "str"
            return
        if e.method in ("partition", "rpartition"):
            # str.(r)partition(sep) -> (before, sep, after): always a 3-tuple
            # of strings, so the unpack targets type as str (prints / == work).
            if len(e.args) != 1:
                raise SemaError(f"str.{e.method}() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
            if A.expr_type(e.args[0]) not in ("str", "any"):
                raise SemaError(f"str.{e.method}() separator must be str", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "tuple"
            e.tuple_elem_types = ["str", "str", "str"]
            return
        if e.method == "join":
            if len(e.args) != 1:
                raise SemaError("str.join() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
            # `sep.join(<any iterable>)`: join consumes a SEQUENCE, so a
            # generator result, a mapping, a set or a str drains through
            # `list()` first -- the same coercion the sequence builtins get,
            # applied to a method argument. `','.join(gen())` was rejected
            # outright.
            _j0 = e.args[0]
            if isinstance(_j0, (A.Name, A.Attr, A.Subscript, A.Call, A.MethodCall)):
                self._check_expr(_j0, scope)
            _jt = A.expr_type(_j0)
            _j_drain = _jt in ("dict", "set")
            if _jt.startswith("instance:"):
                _jc = _jt.split(":", 1)[1]
                _j_drain = (
                    self._resolve_method(_jc, "__next__") is not None
                    or self._resolve_method(_jc, "__getitem__") is not None
                )
            if _j_drain:
                _jd = A.Call(func="list", args=[_j0], kwargs=[], pos=_j0.pos)
                self._check_expr(_jd, scope)
                e.args[0] = _jd
            arg_t = A.expr_type(e.args[0])
            if arg_t not in ("list", "any", "int"):
                # "int" is the default type for unannotated vars; accept it
                # leniently so self-hosting code using e.g. `self.lines` passes.
                raise SemaError("str.join() requires list[str]", e.pos, ErrorCode.E_ARG_TYPE)
            if arg_t == "list":
                arg_el = self._list_el_type(e.args[0], scope)
                # An opaque element kind ("any") is accepted — we can't prove it's
                # str, but join only ever runs on str elements in practice.
                if arg_el not in ("str", "any"):
                    raise SemaError(
                        f"str.join() requires list[str], got list[{arg_el}]", e.pos,
                        ErrorCode.E_ARG_TYPE,
                    )
            e.inferred_type = "str"
            return
        if e.method in ("strip", "lstrip", "rstrip"):
            # Optional `chars` argument (a str). With no arg, strips whitespace.
            if len(e.args) > 1:
                raise SemaError(f"str.{e.method}() takes 0 or 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
            if e.args and A.expr_type(e.args[0]) != "str":
                raise SemaError(f"str.{e.method}() argument must be str", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "str"
            return
        if e.method == "zfill":
            if len(e.args) != 1:
                raise SemaError("str.zfill() takes 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
            if A.expr_type(e.args[0]) not in ("int", "any"):
                raise SemaError("str.zfill() argument must be an int", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "str"
            return
        if e.method in ("ljust", "rjust", "center"):
            # str.{ljust,rjust,center}(width[, fillchar]); fillchar defaults
            # to a space when omitted.
            if len(e.args) not in (1, 2):
                raise SemaError(f"str.{e.method}() takes 1 or 2 arguments", e.pos, ErrorCode.E_ARG_COUNT)
            if A.expr_type(e.args[0]) not in ("int", "any"):
                raise SemaError(f"str.{e.method}() width must be an int", e.pos, ErrorCode.E_ARG_TYPE)
            if len(e.args) == 2 and A.expr_type(e.args[1]) not in ("str", "any"):
                raise SemaError(f"str.{e.method}() fillchar must be a str", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "str"
            return
        if e.method in ("find", "rfind", "index", "rindex"):
            # str.find(sub[, start[, end]]): asmpython supports 1- or 2-arg form.
            if len(e.args) not in (1, 2):
                raise SemaError(f"str.{e.method}() takes 1 or 2 arguments", e.pos, ErrorCode.E_ARG_COUNT)
            if A.expr_type(e.args[0]) not in ("str", "any"):
                raise SemaError(f"str.{e.method}() sub must be a str", e.pos, ErrorCode.E_ARG_TYPE)
            if len(e.args) == 2 and A.expr_type(e.args[1]) not in ("int", "any"):
                raise SemaError(f"str.{e.method}() start must be an int", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "int"
            return
        if e.method == "expandtabs":
            # str.expandtabs([tabsize=8])
            if len(e.args) > 1:
                raise SemaError("str.expandtabs() takes 0 or 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
            if e.args and A.expr_type(e.args[0]) not in ("int", "any"):
                raise SemaError("str.expandtabs() tabsize must be an int", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "str"
            return
        if e.method == "format" and isinstance(e.obj, A.StrLit):
            # `"...".format(args)` with a literal format string: codegen lowers
            # this to a concat chain, so the result is a real str.
            for a in e.args:
                self._check_expr(a, scope)
            for _, a in e.kwargs:
                self._check_expr(a, scope)
            kwarg_names = set()
            for name, _ in e.kwargs:
                kwarg_names.add(name)
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
                            ErrorCode.E_FORMAT_FIELD_UNSUPPORTED,
                        )
                    if val not in kwarg_names:
                        raise SemaError(
                            f"str.format() got an unexpected field name {val!r}",
                            e.pos,
                            ErrorCode.E_FORMAT_UNKNOWN_FIELD,
                        )
                elif val >= len(e.args):
                    raise SemaError(
                        f"str.format() field index {val} out of range "
                        f"({len(e.args)} positional argument(s))",
                        e.pos,
                        ErrorCode.E_FORMAT_INDEX_RANGE,
                    )
            e.inferred_type = "str"
            return
        # Use module-level dicts (not self.STR_METHODS class var) to avoid the
        # opaque-len crash: under a self-compiled sema, dict.get() returns
        # "any"-typed, so len(arg_types_tuple) falls back to _emit_strlen on
        # the tuple header, reading the capacity field as a C string (always 1).
        # Two flat dicts with `: int` / `: str` annotations give correct types.
        _n_expected: int = _STR_METHOD_ARGC.get(e.method, -1)
        if _n_expected < 0:
            # Unmodeled method on a str-typed value (e.g. `e.format(...)` when
            # `e` is a caught exception typed as str). Stay lenient: check args
            # for side effects but treat the result as opaque.
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = "any"
            return
        if e.method == "translate" and len(e.args) == 1:
            # `s.translate(table)` where table came from str.maketrans().
            self._check_expr(e.args[0], scope)
            if A.expr_type(e.args[0]) not in ("dict", "any"):
                raise SemaError(
                    "str.translate() table must be a str.maketrans() mapping",
                    e.pos,
                    ErrorCode.E_ARG_TYPE,
                )
            e.inferred_type = "str"
            return
        if e.method == "replace" and len(e.args) == 3:
            # `s.replace(old, new, count)` -- the optional occurrence limit.
            for _ra in e.args:
                self._check_expr(_ra, scope)
            if (
                A.expr_type(e.args[0]) not in ("str", "any")
                or A.expr_type(e.args[1]) not in ("str", "any")
            ):
                raise SemaError(
                    "str.replace() old/new must be str", e.pos, ErrorCode.E_ARG_TYPE
                )
            if A.expr_type(e.args[2]) not in ("int", "any"):
                raise SemaError(
                    "str.replace() count must be an int", e.pos, ErrorCode.E_ARG_TYPE
                )
            e.inferred_type = "str"
            return
        if e.method in ("strip", "lstrip", "rstrip") and len(e.args) == 1:
            # `s.strip(chars)` strips any character in `chars` from the ends
            # (CPython) rather than whitespace. The no-argument form stays on
            # the runtime helper; this one is lowered as a scan.
            self._check_expr(e.args[0], scope)
            if A.expr_type(e.args[0]) not in ("str", "any"):
                raise SemaError(
                    f"str.{e.method}() argument 1: expected str, "
                    f"got {A.expr_type(e.args[0])}",
                    e.pos,
                    ErrorCode.E_ARG_TYPE,
                )
            e.inferred_type = "str"
            return
        if len(e.args) != _n_expected:
            raise SemaError(
                f"str.{e.method}() takes {_n_expected} argument(s), got {len(e.args)}",
                e.pos,
                ErrorCode.E_ARG_COUNT,
            )
        # str.startswith/endswith also accept a TUPLE of candidate prefixes/
        # suffixes -- True if the string matches ANY of them (CPython). ir_lower
        # iterates the tuple's str elements and ORs the per-element result.
        if e.method in ("startswith", "endswith") and len(e.args) == 1:
            self._check_expr(e.args[0], scope)
            if A.expr_type(e.args[0]) == "tuple":
                e.inferred_type = _STR_METHOD_RET.get(e.method, "int")
                return
        # All str methods that accept arguments require str-typed values.
        _si = 0
        for a in e.args:
            self._check_expr(a, scope)
            got: str = A.expr_type(a)
            if got not in ("str", "any"):
                raise SemaError(
                    f"str.{e.method}() argument {_si + 1}: expected str, got {got}",
                    e.pos,
                    ErrorCode.E_ARG_TYPE,
                )
            _si = _si + 1
        _ret: str = _STR_METHOD_RET.get(e.method, "str")
        e.inferred_type = _ret

    def _apply_ffi_element_return(self, fn, e, scope: Scope) -> None:
        """Type a binding declared `ret_from_element=N` as the element kind of
        its Nth argument -- `random.choice(seq)` returns one of seq's items, not
        a value of a fixed kind. Its declared `ret_type` stays the fallback for
        a source whose element kind isn't tracked."""
        _idx = getattr(fn, "ret_from_element", None)
        if _idx is None or _idx >= len(e.args):
            return
        _src = e.args[_idx]
        if A.expr_type(_src) not in ("list", "tuple"):
            return
        _el = self._list_el_type(_src, scope)
        if _el in ("", "int", "any", "?"):
            return
        e.inferred_type = _el
        if _el == "tuple":
            _slots = self._list_el_tuple_types(_src, scope)
            if _slots:
                e.tuple_elem_types = list(_slots)

    def _fold_variadic_ffi(self, fn, e, args: list) -> list:
        """Fold an N-argument call to an ASSOCIATIVE binary FFI binding into
        nested binary calls: `gcd(a, b, c)` -> `gcd(gcd(a, b), c)`.

        CPython's `math.gcd`/`lcm`/`hypot` are variadic; the C symbols behind
        them are binary. Folding is exact for all three. Handles both call
        shapes -- the bare `gcd(...)` a `from math import gcd` produces, and
        the `math.gcd(...)` module-qualified form -- by rebuilding a node of
        whichever shape it was given, so this stays one mechanism rather than
        one per call syntax.
        """
        if not getattr(fn, "variadic_fold", False):
            return args
        _want = len(getattr(fn, "arg_types", []))
        if _want != 2 or len(args) <= 2:
            return args
        acc = args[0]
        for nxt in args[1:]:
            if isinstance(e, A.MethodCall):
                acc = A.MethodCall(
                    obj=e.obj, method=e.method, args=[acc, nxt],
                    kwargs=[], pos=e.pos,
                )
            else:
                acc = A.Call(
                    func=e.func, args=[acc, nxt], kwargs=[], pos=e.pos,
                )
        # `acc` is the OUTERMOST call; this node takes over its argument pair.
        return list(acc.args)

    def _check_ffi_call(
        self, fn: stdlib.Func, args: list, pos, scope: Scope, *, label: str
    ) -> None:
        """Validate an FFI call's arity and arg types. Performs implicit
        int->float promotion at the call site (so the user can write
        `math.sqrt(4)` without writing `4.0`)."""
        _fn_arg_types: list = getattr(fn, "arg_types", [])
        # Pad a short call from the binding's declared trailing defaults, so an
        # FFI binding can stand in for a CPython function with optional tail
        # parameters (see `Func.defaults`). Mutates `args` in place because the
        # caller passes the live `e.args` list -- codegen needs the full
        # positional list the C symbol actually expects.
        _fn_defaults: tuple = tuple(getattr(fn, "defaults", ()) or ())
        if _fn_defaults and len(args) < len(_fn_arg_types):
            _missing = len(_fn_arg_types) - len(args)
            if _missing <= len(_fn_defaults):
                for _dv in _fn_defaults[len(_fn_defaults) - _missing:]:
                    if isinstance(_dv, bool):
                        args.append(A.IntLit(value=1 if _dv else 0, pos=pos))
                    elif isinstance(_dv, float):
                        args.append(A.FloatLit(value=_dv, pos=pos))
                    elif isinstance(_dv, int):
                        args.append(A.IntLit(value=_dv, pos=pos))
                    else:
                        args.append(A.StrLit(value=str(_dv), pos=pos))
        if len(args) != len(_fn_arg_types):
            raise SemaError(
                f"{label}() takes {len(_fn_arg_types)} argument(s), got {len(args)}",
                pos,
                ErrorCode.E_ARG_COUNT,
            )
        _ffi_i = 0
        for a, want in zip(args, _fn_arg_types):
            want_s: str = want
            self._check_expr(a, scope)
            got = A.expr_type(a)
            if want_s == "any" or got == want_s or got == "any":
                _ffi_i = _ffi_i + 1
                continue
            # Allow int -> float promotion.
            if want_s == "float" and got == "int":
                _ffi_i = _ffi_i + 1
                continue
            # "list_buf": pass a list[int]'s underlying data buffer as a raw
            # pointer (see _gen_ffi_call) -- used for FFI calls that fill a
            # fixed-size struct (e.g. `stat`) the caller reads back as int64
            # words, since string buffers can't survive embedded NUL bytes.
            if want_s == "list_buf" and got == "list" and getattr(a, "list_el_type", "int") == "int":
                _ffi_i = _ffi_i + 1
                continue
            raise SemaError(
                f"{label}() argument {_ffi_i + 1}: expected {want_s}, got {got}",
                pos,
                ErrorCode.E_ARG_TYPE,
            )

    def _maybe_bind_method_args(self, e: A.MethodCall, obj_t: str) -> None:
        """Bind keyword/vararg args on a user-class method call (or super())
        onto positions. No-op for str/list/dict/external methods, which don't
        take keyword args in asmpython's model."""
        sig: "FuncSig | None" = None
        if obj_t.startswith("instance:"):
            r = self._resolve_method(obj_t.split(":", 1)[1], e.method)
            if r is not None:
                sig = r[1]
        elif obj_t.startswith("super:"):
            r = self._resolve_method(obj_t.split(":", 1)[1], e.method)
            if r is not None:
                sig = r[1]
        if sig is None:
            return
        sig_decorators2: list[str] = getattr(sig, "decorators", [])
        is_static_m = "staticmethod" in sig_decorators2
        skip = 0 if is_static_m else 1
        # Explicit `: list` reads before slicing: same opaque-slice bug as
        # the constructor call site above -- sig.param_names/param_defaults
        # read "any" here too (sig itself came from an IfExp/conditional
        # subscript a few lines up), so slicing them went through
        # _gen_subscript's string-slice fallback instead of the real
        # list-slice path.
        sig_param_names: list = sig.param_names
        sig_param_defaults: list = sig.param_defaults
        self._bind_args(
            e,
            sig_param_names[skip:],
            sig_param_defaults[skip:],
            sig.vararg,
            e.pos,
            e.method,
            kwarg=sig.kwarg,
        )

    def _clone_default_expr(self, e):
        """Fresh-identity copy of a parsed default-value expression.

        The parser only accepts literals here (int/float/str/True/False/
        None/list/dict — see parser.py's "default argument must be a
        literal" check), so this only needs to handle those node kinds.
        `copy.deepcopy` would also work under CPython, but asmpython's own
        bundled `copy` module (used once this file is self-compiled) only
        implements deepcopy for actual `list` values — calling it on an AST
        node reads the node's memory through the list-header layout
        (len/buf offsets) instead of its real fields, corrupting whichever
        call site relied on the default. Building fresh nodes by hand here
        works identically whether this is interpreted (gen0) or
        self-compiled (gen1).
        """
        if isinstance(e, A.IntLit):
            return A.IntLit(value=e.value, pos=e.pos, is_bool=e.is_bool, is_none=e.is_none)
        if isinstance(e, A.FloatLit):
            return A.FloatLit(value=e.value, label=e.label, pos=e.pos)
        if isinstance(e, A.StrLit):
            return A.StrLit(value=e.value, label=e.label, pos=e.pos)
        if isinstance(e, A.ListLit):
            cloned_elems: list = []
            for el in e.elems:
                cloned_elems.append(self._clone_default_expr(el))
            return A.ListLit(
                elems=cloned_elems,
                pos=e.pos,
                el_type=e.el_type,
                el_value_type=e.el_value_type,
                el_tuple_types=list(e.el_tuple_types),
            )
        if isinstance(e, A.DictLit):
            cloned_keys: list = []
            for k in e.keys:
                cloned_keys.append(self._clone_default_expr(k) if k is not None else None)
            cloned_values: list = []
            for v in e.values:
                cloned_values.append(self._clone_default_expr(v))
            return A.DictLit(
                keys=cloned_keys,
                values=cloned_values,
                pos=e.pos,
                value_type=e.value_type,
                inner_value_type=e.inner_value_type,
                value_tuple_elem_types=list(e.value_tuple_elem_types),
            )
        # Anything else (shouldn't occur for a literal default) shares
        # identity with the original node rather than crashing.
        return e

    def _bind_args(
        self,
        e: A.Expr,
        names: list[str],
        defaults: list,
        vararg,
        pos,
        label,
        kwarg=None,
    ) -> None:
        """Rewrite a call's (positional, keyword) arguments into a single
        positional list matching `names`, so codegen sees an ordinary call.

        `names`/`defaults` exclude `self` (callers trim it for methods).
        Keyword args are matched onto positions by name; omitted params fall
        back to their default. With a `*args` parameter (the trailing slot),
        surplus positionals are packed into a ListLit passed in that slot.
        With a `**kwargs` parameter, excess keyword arguments are packed into
        a DictLit passed as a final trailing dict-typed slot.
        """
        # Extract args/kwargs via isinstance narrowing: Call and MethodCall
        # have these fields at different offsets, so a typed local is needed.
        if isinstance(e, A.MethodCall):
            _em: A.MethodCall = e
            _e_args: list = _em.args
            _e_kwargs: list = _em.kwargs
        else:
            _ec: A.Call = e
            _e_args: list = _ec.args
            _e_kwargs: list = _ec.kwargs
        # Strip trailing special slots (*args and/or **kwargs) from the fixed param list.
        tail = (1 if vararg is not None else 0) + (1 if kwarg is not None else 0)
        fixed_names = names[:-tail] if tail else names
        fixed_defaults = defaults[:-tail] if tail else defaults
        nfixed = len(fixed_names)
        # Pre-size the slot list with None placeholders. Built with an explicit
        # loop rather than `[None] * nfixed` so this stays self-compilable
        # (asmpython has no list-repeat operator).
        slots: list = []
        for _ in range(nfixed):
            slots.append(None)
        extra: list = []
        for i, a in enumerate(_e_args):
            if i < nfixed:
                slots[i] = a
            elif vararg is not None:
                extra.append(a)
            else:
                raise SemaError(
                    f"{label}() takes {nfixed} argument(s), got {len(_e_args)}", pos,
                    ErrorCode.E_ARG_COUNT,
                )
        excess_kw: list = []
        for kname, kexpr in _e_kwargs:
            if kname not in fixed_names:
                if kwarg is not None:
                    excess_kw.append((kname, kexpr))
                else:
                    raise SemaError(
                        f"{label}() got an unexpected keyword argument {kname!r}", pos,
                        ErrorCode.E_ARG_COUNT,
                    )
            else:
                idx = fixed_names.index(kname)
                if slots[idx] is not None:
                    raise SemaError(
                        f"{label}() got multiple values for argument {kname!r}", pos,
                        ErrorCode.E_ARG_COUNT,
                    )
                slots[idx] = kexpr
        for i in range(nfixed):
            if slots[i] is None:
                if fixed_defaults[i] is not None:
                    # Deep-copy: each omitted-argument call site needs its own
                    # AST node identity. Codegen keys per-literal scratch frame
                    # slots off id(expr) (e.g. _gen_dict_lit's __dictlit_{id(e)}),
                    # so two call sites sharing one default node would collide
                    # on the same slot and corrupt each other's container.
                    slots[i] = self._clone_default_expr(fixed_defaults[i])
                else:
                    raise SemaError(
                        f"{label}() missing required argument {fixed_names[i]!r}",
                        pos,
                        ErrorCode.E_ARG_COUNT,
                    )
        new_args = list(slots)
        if vararg is not None:
            if any(isinstance(_x, A.Starred) for _x in extra):
                # `f(1, *rest)` into a `*args` parameter: the packed slot is
                # the concatenation of the plain surplus arguments and each
                # starred sequence, built with the ordinary list `+` so no new
                # lowering is needed. A `*seq` is materialized through `list()`
                # rather than aliased, matching CPython -- the callee's `args`
                # tuple is a fresh object, so mutating it must not touch `seq`.
                _parts: list = []
                _run: list = []
                for _x in extra:
                    if isinstance(_x, A.Starred):
                        if _run:
                            _parts.append(A.ListLit(elems=_run, pos=pos))
                            _run = []
                        _parts.append(
                            A.Call(func="list", args=[_x.value], kwargs=[], pos=pos)
                        )
                    else:
                        _run.append(_x)
                if _run:
                    _parts.append(A.ListLit(elems=_run, pos=pos))
                _packed = _parts[0]
                for _extra_part in _parts[1:]:
                    _packed = A.BinOp(op="+", left=_packed, right=_extra_part, pos=pos)
                new_args.append(_packed)
            else:
                new_args.append(A.ListLit(elems=extra, pos=pos))
        if kwarg is not None:
            kw_keys: list = []
            kw_vals: list = []
            for kname, kexpr in excess_kw:
                kw_keys.append(A.StrLit(value=kname, pos=pos))
                kw_vals.append(kexpr)
            new_args.append(A.DictLit(keys=kw_keys, values=kw_vals, pos=pos, value_type="any"))
        if isinstance(e, A.MethodCall):
            _em2: A.MethodCall = e
            _em2.args = new_args
            _em2.kwargs = []
        else:
            _ec2: A.Call = e
            _ec2.args = new_args
            _ec2.kwargs = []

    def _desugar_starred_print(self, e: A.Call, scope: Scope) -> None:
        """`print(*seq, sep=S)` -- print a sequence whose LENGTH is only known
        at runtime.

        print's lowering bakes its argument count into the printf format
        string, so a runtime-length argument list can't go through it. Rather
        than add a second, loop-shaped print lowering, rewrite the call into
        one the existing machinery already handles exactly:

            print(a, *seq, b, sep=S, end=E)
              ->  print(S.join([str(a)] + [str(_v) for _v in seq] + [str(b)]),
                        end=E)

        `str.join` over a comprehension is all pre-existing, so this is
        general over any mix of plain and starred arguments and any separator,
        including the default " ".
        """
        sep_val = " "
        kept_kwargs: list = []
        for kn, kv in e.kwargs:
            if kn == "sep" and isinstance(kv, A.StrLit):
                sep_val = kv.value
            else:
                kept_kwargs.append((kn, kv))
        parts: list = []
        run: list = []
        for a in e.args:
            if isinstance(a, A.Starred):
                if run:
                    parts.append(A.ListLit(elems=list(run), pos=e.pos))
                    run = []
                var = f"_pv{len(parts)}"
                parts.append(
                    A.Comprehension(
                        elt=A.Call(
                            func="str",
                            args=[A.Name(name=var, pos=a.pos)],
                            kwargs=[],
                            pos=a.pos,
                        ),
                        var=var,
                        iter=a.value,
                        pos=a.pos,
                        list_el_type="str",
                    )
                )
            else:
                run.append(A.Call(func="str", args=[a], kwargs=[], pos=e.pos))
        if run:
            parts.append(A.ListLit(elems=list(run), pos=e.pos))
        joined_src = parts[0]
        for extra in parts[1:]:
            joined_src = A.BinOp(op="+", left=joined_src, right=extra, pos=e.pos)
        e.args = [
            A.MethodCall(
                obj=A.StrLit(value=sep_val, pos=e.pos),
                method="join",
                args=[joined_src],
                kwargs=[],
                pos=e.pos,
            )
        ]
        e.kwargs = kept_kwargs

    def _starred_expand_count(self, args: list, callee: "Optional[str]") -> "Optional[int]":
        """How many positional arguments a lone `*expr` must supply, or None.

        Known only when the callee is a plain top-level function with a fixed
        parameter list: the count is its parameters minus the plain arguments
        already present. A callee with its own `*vararg` is excluded -- the
        argument count is genuinely unknown there, which is the case that still
        needs real runtime varargs.
        """
        if callee is None or callee not in self.funcs:
            return None
        sig = self.funcs[callee]
        if getattr(sig, "vararg", None):
            return None
        n_starred = sum(1 for a in args if isinstance(a, A.Starred))
        if n_starred != 1:
            return None
        need = sig.arity - (len(args) - 1)
        return need if need > 0 else None

    def _expand_starred_args(self, args: list, scope: Scope, callee: "Optional[str]" = None) -> list:
        """Rewrite `*expr` call arguments in place into one Subscript per
        tuple slot (`expr[0], expr[1], ...`), since asmpython has no runtime
        varargs. Returns the (possibly unchanged) args list."""
        if not any(isinstance(a, A.Starred) for a in args):
            return args
        self._last_starred_dynamic = False
        new_args: list = []
        for a in args:
            if not isinstance(a, A.Starred):
                new_args.append(a)
                continue
            self._check_expr(a.value, scope)
            ets = self._tuple_elem_types(a.value, scope)
            if not ets:
                # No compile-time slot shape (a LIST, or a tuple whose kinds
                # aren't tracked). If the callee is a known function with a
                # fixed parameter count, the number of arguments to produce IS
                # known even though the sequence's contents aren't, so expand to
                # that many subscripts -- `point(*coords)` becomes
                # `point(coords[0], coords[1])`. A shorter sequence at runtime
                # then raises IndexError from the ordinary subscript bounds
                # check, where CPython would raise TypeError.
                # The callee declares `*args`. A `*seq` argument that lands in
                # that slot needs no unpacking AT ALL: the vararg slot is
                # itself a list, so the sequence can be handed over whole.
                # Leave the Starred in place and let `_bind_args` pack it --
                # this is the case `_starred_expand_count` deliberately
                # excludes, because the argument COUNT is unknown; what makes
                # it work anyway is that the count never has to be known.
                if (
                    callee is not None
                    and callee in self.funcs
                    and getattr(self.funcs[callee], "vararg", None)
                    and A.expr_type(a.value) in ("list", "tuple", "any")
                ):
                    new_args.append(a)
                    continue
                _n_star = self._starred_expand_count(args, callee)
                if _n_star is not None and A.expr_type(a.value) in (
                    "list", "tuple", "any",
                ):
                    for i in range(_n_star):
                        sub = A.Subscript(
                            obj=a.value, index=A.IntLit(value=i, pos=a.pos), pos=a.pos
                        )
                        self._check_expr(sub, scope)
                        new_args.append(sub)
                    continue
                # The callee is a CALLABLE VALUE, not a statically known
                # function -- `def wrap(*a): return f(*a)`, the decorator
                # forwarding shape. Neither its arity nor the sequence's length
                # is a compile-time fact, but BOTH are known at runtime, so the
                # call site dispatches on `len(seq)` (see ir_lower's
                # `starred_dynamic` handling). Leave the Starred for it.
                if (
                    callee is not None
                    and callee not in self.funcs
                    and callee not in self.classes
                    and callee in scope.types
                    and A.expr_type(a.value) in ("list", "tuple", "any")
                ):
                    new_args.append(a)
                    self._last_starred_dynamic = True
                    continue
                raise SemaError(
                    "*expr argument unpacking requires a tuple with known "
                    "element types",
                    a.pos,
                    ErrorCode.E_VARARGS_UNPACK,
                )
            for i in range(len(ets)):
                sub = A.Subscript(
                    obj=a.value, index=A.IntLit(value=i, pos=a.pos), pos=a.pos
                )
                self._check_expr(sub, scope)
                new_args.append(sub)
        return new_args

    def _extract_dstar(self, e: A.Call) -> None:
        """Pull a trailing `**expr` (parsed into `args` as a DoubleStarred,
        since the parser doesn't know the callee yet) out into `e.dstar`,
        matching Python's rule that `**kwargs` is always last. Expansion into
        real kwargs happens later, once the callee's param names are known
        (see `_expand_dstar_kwarg`)."""
        if not e.args or not isinstance(e.args[-1], A.DoubleStarred):
            return
        ds: A.DoubleStarred = e.args[-1]
        if any(isinstance(a, A.DoubleStarred) for a in e.args[:-1]):
            raise SemaError("call takes at most one **expr argument", ds.pos, ErrorCode.E_ARG_COUNT)
        e.dstar = ds.value
        e.args = e.args[:-1]

    def _expand_dstar_kwarg(self, e: A.Call, param_names: list, scope: Scope) -> None:
        """Expand `e.dstar` (a `**expr` call argument) into `e.kwargs`, given
        the callee's declared parameter names (`self` / bound receiver
        already excluded by the caller). Each declared name not already
        bound positionally or by an explicit keyword becomes
        `name=dstar["name"]`. No-op if there's no dstar to expand."""
        if e.dstar is None:
            return
        self._check_expr(e.dstar, scope)
        dstar_t = A.expr_type(e.dstar)
        if dstar_t not in ("dict", "any"):
            raise SemaError(
                "**expr call argument must be a dict", e.dstar.pos,
                ErrorCode.E_DSTAR_NOT_DICT,
            )
        already_bound = set(param_names[: len(e.args)]) | {kn for kn, _ in e.kwargs}
        for name in param_names:
            if name in already_bound:
                continue
            sub = A.Subscript(
                obj=e.dstar, index=A.StrLit(value=name, pos=e.pos), pos=e.pos
            )
            self._check_expr(sub, scope)
            e.kwargs.append((name, sub))
        e.dstar = None

    def _callable_type_of(self, e, scope: Scope) -> "str | None":
        """The `callable:<ret>` descriptor for an expression that denotes a
        CALLABLE VALUE, or None if it doesn't denote one.

        A callable value at runtime is just a code pointer (`A.Lambda` and a
        bare function reference both lower to `global_addr <symbol>`), so it
        rides in any 8-byte slot -- a variable, a dict value, a list element,
        an instance field, a parameter. What was missing was a TYPE for it, so
        a read back out of one of those slots could be recognized as callable
        again. Encoding the return kind INTO the type string (`callable:int`,
        `callable:str`) is what makes that uniform: every existing single-string
        type channel -- `scope.types`, `dict_value_types`, `list_el_type`,
        field types, `-> ` annotations -- carries it with no per-container
        bookkeeping, so `dict[str, callable]` and `list[callable]` and
        `self.fn` all work off one mechanism rather than one special case each.
        """
        if isinstance(e, A.Lambda):
            return "callable:" + (getattr(e, "lambda_ret", None) or "int")
        if isinstance(e, A.Name):
            nm = e.name
            # A local binding always wins over a same-named global function
            # (ordinary LEGB), and its own type may already BE a callable.
            _t = scope.types.get(nm)
            if isinstance(_t, str) and _t.startswith("callable:"):
                return _t
            if nm in self.lambda_rets and nm not in self.funcs:
                return "callable:" + (self.lambda_rets[nm] or "int")
            if nm not in scope.types and nm in self.funcs:
                _sig = self.funcs[nm]
                _rt = getattr(_sig, "ret_type", None)
                return "callable:" + (_rt[0] if _rt else "int")
            return None
        # A read out of a container/field: the callable descriptor was stored
        # as that slot's element/value/field kind, so just read it back.
        _et = A.expr_type(e)
        if isinstance(_et, str) and _et.startswith("callable:"):
            return _et
        return None

    def _check_callable_expr_call(self, e: A.Call, scope: Scope) -> None:
        """`<expr>(args)` -- a call whose callee is an expression, not a name.

        The callee is checked as an ordinary expression, its `callable:<ret>`
        descriptor decides the call's result type, and ir_lower emits an
        indirect call through the resulting code pointer.
        """
        callee = e.func_expr
        # `type(x)(args)` -- construct another instance of x's class. asmpython
        # has no runtime class objects (see the `cls(...)` rewrite in
        # _check_call), so a type value is only ever as precise as the static
        # type of what it was taken from: resolve it to that class and let the
        # ordinary constructor path handle the call. Done BEFORE checking the
        # callee, so `type(x)` never has to become a callable value at all.
        if (
            isinstance(callee, A.Call)
            and callee.func == "type"
            and len(callee.args) == 1
            and not callee.kwargs
        ):
            self._check_expr(callee.args[0], scope)
            _at = A.expr_type(callee.args[0])
            if isinstance(_at, str) and _at.startswith("instance:"):
                _cls_of = _at.split(":", 1)[1]
                if _cls_of in self.classes:
                    e.func = _cls_of
                    e.func_expr = None
                    self._check_call(e, scope)
                    return
        self._check_expr(callee, scope)
        # A callee that is an INSTANCE with `__call__` (`make_factory()(42)`,
        # `registry[k](x)` over a list of functors): dispatch to the class's
        # own `__call__`, exactly as the name-callee path does a few hundred
        # lines below -- the only difference is that the receiver is an
        # expression, so ir_lower evaluates it instead of loading a slot.
        _ct = A.expr_type(callee)
        if isinstance(_ct, str) and _ct.startswith("instance:"):
            _ccls = _ct.split(":", 1)[1]
            _cres = self._resolve_method(_ccls, "__call__")
            if _cres is not None:
                _cowner: str = _cres[0]
                _csig: FuncSig = _cres[1]
                _cnames: list = _csig.param_names
                _cdefs: list = _csig.param_defaults
                self._bind_args(
                    e, _cnames[1:], _cdefs[1:], _csig.vararg, e.pos,
                    f"{_ccls}.__call__", kwarg=_csig.kwarg,
                )
                for _a in e.args:
                    self._check_expr(_a, scope)
                e.dunder_call_owner = _cowner  # type: ignore
                e.dunder_call_on_expr = True  # type: ignore
                if _csig.ret_type is not None:
                    _rty, _rel, _rval = _csig.ret_type  # type: ignore
                    e.inferred_type = _rty
                    if _rty == "list" and _rel is not None:
                        e.list_el_type = _rel  # type: ignore
                    elif _rty == "dict" and _rval is not None:
                        e.value_type = _rval  # type: ignore
                else:
                    e.inferred_type = "any"
                return
        # A CLOSURE VALUE as the callee (`adder(5)(10)`, `a(1)(2)(3)`): the
        # object is `[magic, fn_ptr, caps...]`, not a code pointer, so it needs
        # the capture-count dispatch rather than a plain indirect call. Marked
        # for ir_lower, which already has that dispatch for the name-callee
        # form.
        if _ct == "closure":
            for _a in e.args:
                self._check_expr(_a, scope)
            e.closure_call_on_expr = True  # type: ignore[attr-defined]
            # NOT propagated as another "closure" when the target is itself a
            # factory: that types a chain like `a(1)(2)(3)` all the way through
            # and it COMPILES, but answers 5 for 1+2+3 -- the transitive
            # capture forwarding through two levels drops one. A clean compile
            # error beats a silently wrong number, so the chain stays out until
            # that forwarding is right.
            e.inferred_type = "any"
            return
        ctype = self._callable_type_of(callee, scope)
        if ctype is None:
            _shown = A.expr_type(callee) or "value"
            raise SemaError(
                f"{_shown!r} is not callable "
                "(only a function, lambda, or a value holding one can be "
                "called; asmpython needs the callable's type to be known "
                "statically at the call site)",
                e.pos,
                ErrorCode.E_NO_METHOD,
            )
        for a in e.args:
            self._check_expr(a, scope)
        for _kn, _kv in e.kwargs:
            self._check_expr(_kv, scope)
        ret = ctype.split(":", 1)[1] or "int"
        # `callable:list[int]` style descriptors keep the element kind so the
        # result of the call is a fully typed list, not a bare `list`.
        if "[" in ret and ret.endswith("]"):
            base, _, el = ret[:-1].partition("[")
            e.inferred_type = base
            if base in ("list", "set", "tuple"):
                e.list_el_type = el  # type: ignore
            elif base == "dict":
                e.value_type = el  # type: ignore
        else:
            e.inferred_type = ret
        e.callable_indirect = True  # type: ignore

    def _check_call(self, e: A.Call, scope: Scope) -> None:
        if getattr(e, "func_expr", None) is not None:
            self._check_callable_expr_call(e, scope)
            return
        self._extract_dstar(e)
        # `cls(...)` inside a @classmethod — asmpython has no runtime class
        # objects, so `cls` always means "the class this classmethod is
        # defined on" (mirrors the existing `cls.field` -> `ClassName.field`
        # rewrites above). Rewriting to the concrete class name here lets it
        # go through the ordinary constructor path, dstar expansion included.
        if (
            self.classmethod_cls_param is not None
            and e.func == self.classmethod_cls_param
            and self.current_class is not None
        ):
            e.func = self.current_class
        # Resolve import alias (from mod import orig as local) for bundled-source
        # stdlib functions, BEFORE any builtin-name dispatch below -- so a
        # locally-aliased name that happens to collide with a builtin (e.g.
        # `from fnmatch import fnfilter as filter`) resolves to the real
        # merged function instead of being treated as the builtin `filter`.
        # Moved here (was previously much further down, after the
        # `BUILTINS`-dispatch block's own early `return`s) because a
        # colliding name never reached the old location at all: `"filter"`
        # matched `BUILTINS` first and returned before alias resolution ever
        # ran. Explicit `: str` read, not a bare `e.func in
        # self.mod.func_aliases` subscript: e is this function's own
        # `A.Call` parameter, an external/opaque type to sema, so e.func
        # reads "any"-typed despite always holding a real string.
        func_name: str = e.func
        if func_name in self.mod.func_aliases and func_name not in self.funcs:
            resolved = self.mod.func_aliases[func_name]
            if resolved in self.funcs:
                e.func = resolved
        if e.func == "list" and len(e.args) == 1:
            _l0 = e.args[0]
            if isinstance(_l0, A.Call) and _l0.func in ("map", "filter"):
                # Mark the one shape ir_lower materializes directly, so the
                # self-wrap below leaves it alone instead of producing
                # `list(list(map(...)))`.
                _l0._in_list_call = True  # type: ignore[attr-defined]
        if (
            e.func == "map"
            and len(e.args) > 2
            and e.func not in self.funcs
            and e.func not in self.mod.func_aliases
        ):
            # `map(f, a, b, ...)` over SEVERAL iterables applies f across them
            # in lockstep, stopping at the shortest. Only the single-iterable
            # form has a lowering, so this crashed. Rewrite it into the
            # comprehension it means:
            #
            #   [f(a[i], b[i]) for i in range(min(len(a), len(b)))]
            #
            # The call on `f` is a call on a callable EXPRESSION, which is now
            # a first-class thing (see `_check_callable_expr_call`), so this
            # needs no new lowering of its own.
            _mm_fn = e.args[0]
            _mm_srcs = list(e.args[1:])
            for _si in range(len(_mm_srcs)):
                self._check_expr(_mm_srcs[_si], scope)
                if A.expr_type(_mm_srcs[_si]) != "list":
                    _wrapped = A.Call(
                        func="list", args=[_mm_srcs[_si]], kwargs=[], pos=e.pos
                    )
                    self._check_expr(_wrapped, scope)
                    _mm_srcs[_si] = _wrapped
            _mm_var = f"_mm{id(e) % 100000}"
            _mm_len = A.Call(
                func="len",
                args=[self._clone_default_expr(_mm_srcs[0])],
                kwargs=[],
                pos=e.pos,
            )
            for _src in _mm_srcs[1:]:
                _mm_len = A.Call(
                    func="min",
                    args=[
                        _mm_len,
                        A.Call(
                            func="len",
                            args=[self._clone_default_expr(_src)],
                            kwargs=[],
                            pos=e.pos,
                        ),
                    ],
                    kwargs=[],
                    pos=e.pos,
                )
            _mm_call = A.Call(
                func=A.CALLABLE_EXPR_SENTINEL,
                args=[
                    A.Subscript(
                        obj=self._clone_default_expr(_src2),
                        index=A.Name(name=_mm_var, pos=e.pos),
                        pos=e.pos,
                    )
                    for _src2 in _mm_srcs
                ],
                kwargs=[],
                func_expr=_mm_fn,
                pos=e.pos,
            )
            _mm_comp = A.Comprehension(
                elt=_mm_call,
                var=_mm_var,
                iter=A.Call(func="range", args=[_mm_len], kwargs=[], pos=e.pos),
                pos=e.pos,
            )
            e.func = "list"
            e.args = [_mm_comp]
            e.kwargs = []
            self._check_call(e, scope)
            return
        if (
            e.func in ("map", "filter")
            and e.func not in self.funcs
            and e.func not in self.mod.func_aliases
            and not getattr(e, "_in_list_call", False)
        ):
            # A `map`/`filter` result is only ever MATERIALIZED by the
            # `list(map(...))` lowering. Used anywhere else --
            # `','.join(map(str, xs))`, `any(map(pred, xs))`,
            # `sum(map(f, xs))`, or just assigned to a name -- the call fell
            # through to a symbol that does not exist and the program
            # segfaulted.
            #
            # asmpython materializes lazy sequences eagerly everywhere else
            # (a generator expression is already an eager list, see
            # A.Comprehension), so the consistent fix is to materialize these
            # too: wrap the call in `list(...)` and let the one existing
            # lowering handle every position uniformly.
            _mf_inner = A.Call(
                func=e.func, args=list(e.args), kwargs=list(e.kwargs), pos=e.pos
            )
            _mf_inner._in_list_call = True  # type: ignore[attr-defined]
            e.func = "list"
            e.args = [_mf_inner]
            e.kwargs = []
            self._check_call(e, scope)
            return
        if (
            e.func == "enumerate"
            and e.func not in self.funcs
            and e.func not in self.mod.func_aliases
        ):
            # `enumerate(seq[, start])` used as a VALUE. It was only ever
            # recognized in a `for` header, so `list(enumerate(xs))` failed with
            # "undefined function 'enumerate'" -- enumerate is not in BUILTINS
            # at all, because that for-position handling consumed it.
            #
            # As a value it materializes the pairs, which is exactly
            #   [(i + start, seq[i]) for i in range(len(seq))]
            # built out of nodes that already exist, so no new lowering is
            # needed. The source is drained through `list()` first, which makes
            # this work uniformly over a str, a mapping, or a generator, not
            # just a list.
            _en_start = None
            for _kn, _kv in list(e.kwargs):
                if _kn == "start":
                    _en_start = _kv
            _en_pos = list(e.args)
            if _en_start is None and len(_en_pos) == 2:
                _en_start = _en_pos[1]
            if len(_en_pos) not in (1, 2):
                raise SemaError(
                    "enumerate() takes 1 or 2 arguments", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            _en_src = _en_pos[0]
            self._check_expr(_en_src, scope)
            if A.expr_type(_en_src) != "list":
                _en_src = A.Call(func="list", args=[_en_src], kwargs=[], pos=e.pos)
                self._check_expr(_en_src, scope)
            _en_var = f"_en{id(e) % 100000}"
            _en_idx = A.Name(name=_en_var, pos=e.pos)
            _en_i = (
                _en_idx
                if _en_start is None
                else A.BinOp(op="+", left=_en_idx, right=_en_start, pos=e.pos)
            )
            _en_elt = A.TupleLit(
                elems=[
                    _en_i,
                    A.Subscript(
                        # Its OWN copy of the source: codegen keys per-literal
                        # scratch slots off id(expr), so sharing one node
                        # between the `len(...)` in the range header and the
                        # element read in the body collides on a single slot.
                        obj=self._clone_default_expr(_en_src),
                        index=A.Name(name=_en_var, pos=e.pos),
                        pos=e.pos,
                    ),
                ],
                pos=e.pos,
            )
            _en_comp = A.Comprehension(
                elt=_en_elt,
                var=_en_var,
                iter=A.Call(
                    func="range",
                    args=[A.Call(func="len", args=[_en_src], kwargs=[], pos=e.pos)],
                    kwargs=[],
                    pos=e.pos,
                ),
                pos=e.pos,
                list_el_type="tuple",
            )
            e.func = "list"
            e.args = [_en_comp]
            e.kwargs = []
            self._check_call(e, scope)
            e.list_el_type = "tuple"
            e.el_tuple_types = [  # type: ignore[attr-defined]
                "int", self._list_el_type(_en_src, scope),
            ]
            return
        if e.func == "print" and any(isinstance(a, A.Starred) for a in e.args):
            self._desugar_starred_print(e, scope)
        self._last_starred_dynamic = False
        e.args = self._expand_starred_args(e.args, scope, callee=e.func)
        if getattr(self, "_last_starred_dynamic", False):
            e.starred_dynamic = True  # type: ignore[attr-defined]
        # Builtins are shadowable: after whole-program merge a call like
        # `re.compile(...)` rewrites to a plain `compile(...)`, and that must
        # resolve to the merged stdlib function, not the interpreter-only
        # builtin of the same name.
        if (
            e.func in INTERPRETER_ONLY_BUILTINS
            and e.func not in self.funcs
            and e.func not in self.classes
            and e.func not in self.ffi_funcs
            and e.func not in scope.types
        ):
            raise SemaError(
                f"{e.func}() is not supported: it requires a Python interpreter "
                "and cannot be compiled to native code",
                e.pos,
                ErrorCode.E_INTERPRETER_ONLY_FEATURE,
            )
        if e.func == "range":
            # range(...) as a value materializes a list[int]. (In a `for` header
            # the parser captures range specially and it never becomes a Call.)
            if not (1 <= len(e.args) <= 3):
                raise SemaError(
                    f"range() takes 1-3 arguments, got {len(e.args)}", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            for a in e.args:
                self._check_expr(a, scope)
                if A.expr_type(a) not in ("int", "any"):
                    raise SemaError("range() arguments must be ints", e.pos, ErrorCode.E_ARG_TYPE)
            e.inferred_type = "list"
            e.list_el_type = "int"
            return
        if e.func == "super":
            # super() — only valid inside a method, takes no args, and resolves
            # to the current class's base. The result carries a `super:<Base>`
            # marker so the enclosing MethodCall dispatches against the base.
            if e.args:
                raise SemaError("super() takes no arguments", e.pos, ErrorCode.E_ARG_COUNT)
            if self.current_class is None:
                raise SemaError("super() outside a method", e.pos, ErrorCode.E_SUPER_NO_CLASS)
            parent = self.classes[self.current_class].parent
            if parent is None:
                raise SemaError(
                    f"{self.current_class!r} has no base class for super()", e.pos,
                    ErrorCode.E_SUPER_NO_CLASS,
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
                    f"isinstance() takes 2 arguments, got {len(e.args)}", e.pos,
                    ErrorCode.E_ARG_COUNT,
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
                    f"getattr() takes 2-3 arguments, got {len(e.args)}", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = "any"
            # `getattr(o, 'name', default)`: when the attribute is NOT a known
            # field of a known class, the result is the default -- so it has the
            # default's type. "any" was a raw pointer with no tag, printing the
            # default string's address as a decimal.
            if len(e.args) == 3:
                _ga_obj_t = A.expr_type(e.args[0])
                _ga_known = False
                if _ga_obj_t.startswith("instance:") and isinstance(e.args[1], A.StrLit):
                    _ga_cls = _ga_obj_t.split(":", 1)[1]
                    _ga_known = (
                        self._resolve_field_type(_ga_cls, e.args[1].value) is not None
                        or self._resolve_method(_ga_cls, e.args[1].value) is not None
                    )
                if not _ga_known:
                    _ga_dflt = A.expr_type(e.args[2])
                    if _ga_dflt not in ("any", ""):
                        e.inferred_type = _ga_dflt
                        if _ga_dflt == "list":
                            e.list_el_type = self._list_el_type(e.args[2], scope)
                        elif _ga_dflt == "dict":
                            e.value_type = self._dict_value_type(e.args[2], scope)
            return
        if e.func == "hasattr":
            # hasattr(obj, "name") -> int 0/1.
            if len(e.args) != 2:
                raise SemaError(
                    f"hasattr() takes 2 arguments, got {len(e.args)}", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            for a in e.args:
                self._check_expr(a, scope)
            e.inferred_type = "int"
            return
        if e.func == "import_binary":
            # import_binary(path) -> a dynamic-module handle (a real runtime
            # LoadLibraryW/dlopen handle). Codegen resolves which functions
            # belong to it from `@<assigned-name>.imported` decorators found
            # elsewhere in the program (Codegen.imported_funcs, built from
            # the whole-program AST) — sema only needs to type the call's
            # result so `handle.some_func(...)` type-checks as a method call
            # on an instance, the same path every other class uses.
            if len(e.args) != 1:
                raise SemaError(
                    f"import_binary() takes 1 argument, got {len(e.args)}", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            self._check_expr(e.args[0], scope)
            if A.expr_type(e.args[0]) != "str":
                raise SemaError(
                    "import_binary() path argument must be a str", e.pos, ErrorCode.E_ARG_TYPE
                )
            e.inferred_type = "instance:DynamicModule"
            return
        if e.func == "gl_import":
            # gl_import() -> a GL-function-pointer-table handle, the same
            # `@<assigned-name>.imported` resolution mechanism as
            # import_binary() (see Codegen.imported_funcs), but resolving
            # each function via SDL_GL_GetProcAddress instead of
            # LoadLibrary+GetProcAddress/dlsym -- the right lookup for
            # OpenGL functions beyond GL 1.1, which aren't necessarily
            # exported directly by opengl32.dll/libGL.so and must be
            # resolved through the active GL context instead. Takes no
            # arguments: unlike import_binary(path), there's no library
            # path -- SDL_GL_GetProcAddress always resolves against
            # whichever GL context is current.
            if len(e.args) != 0:
                raise SemaError(
                    f"gl_import() takes 0 arguments, got {len(e.args)}", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            e.inferred_type = "instance:DynamicModule"
            return
        if e.func == "gl_resolve":
            # gl_resolve(handle, "funcName") -> int (the resolved function
            # pointer, or 0 if it's not a real GL function). Forces the
            # same lazy resolve-and-cache _gen_dynamic_call does on a
            # function's first real call (see gl_import()'s docstring),
            # without actually calling through the pointer -- for a
            # function whose real signature doesn't fit @handle.imported's
            # plain int/float/str/list_buf marshalling (e.g. glShaderSource,
            # which takes a char** and is called through the hand-marshalled
            # gl_shader_source_1 helper instead), where the stub exists only
            # to register the function's resolved pointer, never to be
            # called through directly.
            if len(e.args) != 2:
                raise SemaError(
                    f"gl_resolve() takes 2 arguments, got {len(e.args)}", e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            self._check_expr(e.args[0], scope)
            self._check_expr(e.args[1], scope)
            if A.expr_type(e.args[1]) != "str":
                raise SemaError(
                    "gl_resolve()'s second argument must be a str literal naming "
                    "the function (e.g. \"glShaderSource\")",
                    e.pos, ErrorCode.E_ARG_TYPE,
                )
            e.inferred_type = "int"
            return
        if e.func in BUILTINS:
            lo, hi = BUILTINS[e.func]
            if not (lo <= len(e.args) <= hi):
                if lo == hi:
                    raise SemaError(
                        f"{e.func}() takes {lo} argument(s), got {len(e.args)}",
                        e.pos,
                        ErrorCode.E_ARG_COUNT,
                    )
                raise SemaError(
                    f"{e.func}() takes {lo}-{hi} arguments, got {len(e.args)}",
                    e.pos,
                    ErrorCode.E_ARG_COUNT,
                )
            self._coerce_iterable_instance_args(e, scope)
            if e.func in ("map", "filter") and len(e.args) >= 2:
                # The function is called with the source sequence's elements --
                # tell the lambda so its parameter types correctly (see
                # `param_hint` in _check_expr's A.Lambda case).
                self._check_expr(e.args[1], scope)
                _mf_el = self._iter_element_type(e.args[1], scope)
                if isinstance(e.args[0], A.Name):
                    # `map(str, xs)` / `filter(is_even, xs)`: the lowering calls
                    # a Lambda's synthesized function, so a bare callable NAME
                    # was "unsupported expr Call (map() with a non-lambda
                    # predicate)". Desugar it, exactly as `key=` does.
                    _mf_lam = self._callable_name_as_lambda(
                        e.args[0].name, scope, e.pos, param_hint=_mf_el
                    )
                    if _mf_lam is not None:
                        e.args[0] = _mf_lam
                elif isinstance(e.args[0], A.Lambda):
                    e.args[0].param_hint = _mf_el  # type: ignore[attr-defined]
            for a in e.args:
                self._check_expr(a, scope)
            # `sum` follows its element type: summing a float list/tuple (or
            # passing a float `start`) yields a float, not an int. Computed
            # here so codegen accumulates in xmm (fadd) and the result reads
            # back as a real double instead of the int bits of the last add.
            _sum_ty = "int"
            if e.func == "sum" and e.args:
                _xs = e.args[0]
                _xt = A.expr_type(_xs)
                _el = "int"
                if _xt == "list":
                    _el = self._list_el_type(_xs, scope)
                elif _xt == "tuple":
                    _tts = A.tuple_element_types(_xs)
                    _el = _tts[0] if _tts and all(t == _tts[0] for t in _tts) else "int"
                if _el == "float" or (
                    len(e.args) >= 2 and A.expr_type(e.args[1]) == "float"
                ):
                    _sum_ty = "float"
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
                "sum": _sum_ty,
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
                "bitcast_f2i": "int",
                "bitcast_i2f": "float",
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
                "zip": "list",
                "format": "str",
                "hex": "str",
                "oct": "str",
                "bin": "str",
                "divmod": "tuple",
                "hash": "int",
                "issubclass": "int",
                "bytes": "list",
                "bytearray": "list",
                # Descriptor wrappers -> an opaque tagged cell (see ir_lower's
                # _lower_descriptor_wrapper); "any" keeps isinstance()/
                # `.__func__`/`.fget` reads on the result lenient.
                "staticmethod": "any",
                "classmethod": "any",
                "property": "any",
                # slice object -> an opaque tagged cell usable as a dynamic
                # subscript index (see ir_lower's _lower_slice_ctor).
                "slice": "any",
            }[e.func]
            if e.func in ("map", "filter") and len(e.args) >= 2:
                # The result list's ELEMENT kind: map() yields whatever the
                # mapping function returns, filter() yields the source's own
                # elements. Left untracked, `list(map(str, xs))` typed its
                # elements "int" and printed string pointers as integers.
                if e.func == "filter":
                    e.list_el_type = self._list_el_type(e.args[1], scope)
                elif isinstance(e.args[0], A.Lambda):
                    e.list_el_type = getattr(e.args[0], "lambda_ret", "int")
            if e.func == "abs":
                # abs preserves the operand's numeric type (float -> float so
                # the result prints/operates as a float, not its raw bits).
                arg_t: str = A.expr_type(e.args[0])
                if arg_t.startswith("instance:"):
                    resolved = self._resolve_method(arg_t.split(":", 1)[1], "__abs__")
                    if resolved is not None:
                        sig: FuncSig = resolved[1]
                        if sig.ret_type is not None:
                            rt8: tuple = sig.ret_type  # type: ignore
                            ty: str = rt8[0]
                            el = rt8[1]
                            e.inferred_type = ty  # type: ignore
                            if ty == "list" and el is not None:
                                e.list_el_type = el  # type: ignore
                        else:
                            e.inferred_type = "any"  # type: ignore
                    else:
                        e.inferred_type = "int"  # type: ignore
                else:
                    e.inferred_type = "float" if arg_t == "float" else "int"  # type: ignore
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
            if e.func == "zip":
                # zip(*iterables) -> list of tuples; each arg must be a list/tuple.
                for a in e.args:
                    t = A.expr_type(a)
                    if t not in ("list", "tuple", "any"):
                        raise SemaError("zip() arguments must be lists or tuples", e.pos, ErrorCode.E_ZIP_ARGS)
                e.list_el_type = "tuple"
                e.tuple_elem_types = [self._iter_element_type(a, scope) for a in e.args]
                return
            if e.func == "reversed":
                if len(e.args) != 1:
                    raise SemaError("reversed() takes exactly 1 argument", e.pos, ErrorCode.E_ARG_COUNT)
                src = e.args[0]
                src_t = A.expr_type(src)
                # A class defining `__reversed__` provides its own reversal --
                # rewrite to that call, which is what CPython does. Handled
                # before the type check because the object is not a sequence.
                if src_t.startswith("instance:"):
                    _rv_cls = src_t.split(":", 1)[1]
                    if self._resolve_method(_rv_cls, "__reversed__") is not None:
                        e.__class__ = A.MethodCall  # type: ignore[assignment]
                        e.obj = src  # type: ignore[attr-defined]
                        e.method = "__reversed__"  # type: ignore[attr-defined]
                        e.args = []
                        self._check_expr(e, scope)
                        return
                if src_t not in ("list", "tuple", "str", "any"):
                    raise SemaError(
                        "reversed() argument must be a list, tuple, or str",
                        e.pos,
                        ErrorCode.E_ARG_TYPE,
                    )
                e.inferred_type = "list"
                if src_t == "str":
                    e.list_el_type = "str"
                elif src_t == "tuple":
                    ets = self._tuple_elem_types(src, scope)
                    e.list_el_type = ets[0] if ets and _all_same(ets) else "any"
                else:
                    e.list_el_type = self._list_el_type(src, scope)
                    if e.list_el_type == "tuple":
                        e.tuple_elem_types = self._list_el_tuple_types(src, scope)
                    elif e.list_el_type in ("list", "dict"):
                        e.el_value_type = self._list_el_value_type(src, scope)
                return
            if e.func in ("set", "frozenset"):
                # For set comprehensions, validate the element type.
                if e.args and isinstance(e.args[0], A.Comprehension):
                    comp = e.args[0]
                    et = A.expr_type(comp.elt)
                    if et not in ("str", "int", "any", "tuple"):
                        raise SemaError(
                            f"set elements of type {et} are not supported yet "
                            "(sets are str/int-keyed in v1)",
                            e.pos,
                            ErrorCode.E_SET_KEY_TYPE,
                        )
                return
            if e.func in (
                "bool",
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
                "bitcast_f2i",
                "bitcast_i2f",
            ):
                return
            if e.func in ("min", "max"):
                # min/max: the 1-arg "iterable" form supports key=/reverse=
                # (reverse= is meaningless here but accepted for symmetry with
                # sorted()'s kwarg set — codegen ignores it). The variadic
                # scalar form (min(a, b, ...)) doesn't support key=.
                self._check_sort_kwargs(e, scope, allow_default=True)
                if len(e.args) == 1:
                    e.inferred_type = self._list_el_type(e.args[0], scope)
                    if e.inferred_type == "tuple":
                        e.tuple_elem_types = self._list_el_tuple_types(e.args[0], scope)
                    elif e.inferred_type == "dict":
                        # `max(rows, key=...)` over a list of DICTS returns one
                        # of those dicts, so the result carries their value
                        # kind -- without it `max(...)['name']` read a str value
                        # as an int and printed a pointer.
                        e.value_type = self._list_el_value_type(e.args[0], scope)
                    elif e.inferred_type == "list":
                        e.list_el_type = self._list_el_value_type(e.args[0], scope)
                    _dflt = getattr(e, "minmax_default", None)
                    if _dflt is not None and e.inferred_type in ("int", "?"):
                        # An empty/untyped iterable (e.g. the literal `[]`)
                        # resolves its element kind to the "int" unknown
                        # sentinel. When a default of a concrete non-int kind is
                        # supplied, the result is really that kind
                        # (`min([], default="x")` -> str): adopt it so the
                        # returned value lands in a correctly-typed slot instead
                        # of a str/float being read back as raw int bits.
                        _dflt_t = A.expr_type(_dflt)
                        if _dflt_t not in ("int", "?"):
                            e.inferred_type = _dflt_t
                else:
                    if e.sort_key is not None:
                        raise SemaError(
                            f"{e.func}(): key= is only supported for the "
                            "single-iterable form",
                            e.pos,
                            ErrorCode.E_KEY_UNSUPPORTED_FORM,
                        )
                    if getattr(e, "minmax_default", None) is not None:
                        # CPython: "Cannot specify a default for {min,max}()
                        # with multiple positional arguments" -- default only
                        # makes sense for the possibly-empty iterable form.
                        raise SemaError(
                            f"{e.func}(): default= is only supported for the "
                            "single-iterable form",
                            e.pos,
                            ErrorCode.E_KEY_UNSUPPORTED_FORM,
                        )
                    types = set()
                    for a in e.args:
                        types.add(A.expr_type(a))
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
                    if e.list_el_type == "tuple":
                        # Carry the per-slot kinds through: sorting a list of
                        # pairs returns a list of the SAME pairs, and the repr
                        # needs those kinds to format each slot (without them
                        # the result falls back to the dict-items assumption
                        # and an (int, str) pair crashes -- see
                        # `_lower_list_of_tuples_repr`).
                        e.el_tuple_types = self._list_el_tuple_types(e.args[0], scope)
                return
            if e.func == "tuple":
                # tuple(x): a shallow copy in the shared list/tuple layout. The
                # per-slot kinds aren't tracked (source may be any iterable), so
                # downstream indexing/unpacking stays lenient.
                t = A.expr_type(e.args[0])
                if t not in ("list", "tuple", "str", "any", "int"):
                    raise SemaError(
                        "tuple() requires a list, tuple, or string", e.pos,
                        ErrorCode.E_ARG_TYPE,
                    )
                return
            if e.func == "list":
                # list(x) yields a list; carry the source's element kind so
                # later `for el in list(x)` / indexing pick the right register.
                t = A.expr_type(e.args[0])
                if t.startswith("instance:"):
                    # An iterable object: a user class with __iter__/__next__,
                    # which is also the shape a generator function's result
                    # takes (sema desugars `yield` into exactly such a class).
                    # `list(gen())` / `list(deque_obj)` drains it -- lowered by
                    # running the iterator protocol.
                    _lc = t.split(":", 1)[1]
                    _lnx = self._resolve_method(_lc, "__next__")
                    if _lnx is None:
                        # No iterator protocol -- accept the sequence protocol
                        # instead (`__len__` + `__getitem__`, what deque and
                        # other container classes provide); the lowering walks
                        # it by index.
                        _lnx = self._resolve_method(_lc, "__getitem__")
                        if _lnx is None or self._resolve_method(_lc, "__len__") is None:
                            raise SemaError(
                                f"list() argument {_lc} is not iterable "
                                "(needs __next__, or __len__ and __getitem__)",
                                e.pos,
                                ErrorCode.E_ARG_TYPE,
                            )
                    _lrt = getattr(_lnx[1], "ret_type", None)
                    e.list_el_type = _lrt[0] if isinstance(_lrt, tuple) and _lrt else "any"
                    return
                if t not in ("list", "tuple", "str", "dict", "set", "any"):
                    raise SemaError(
                        "list() requires a list, tuple, dict, set, or string",
                        e.pos,
                        ErrorCode.E_ARG_TYPE,
                    )
                if t in ("dict", "set", "str"):
                    # Iterating a dict/set yields its (str) keys; iterating a
                    # str yields 1-character strings.
                    e.list_el_type = "str"
                    return
                e.list_el_type = self._list_el_type(e.args[0], scope)
                # Propagate per-slot tuple types so `for a, b in list(zip(...))` works.
                tup_types = self._list_el_tuple_types(e.args[0], scope)
                if tup_types:
                    e.tuple_elem_types = tup_types
                return
            if e.func == "dict":
                # dict() / dict(other) -> a (shallow-copied) dict. Carry the
                # source's value kind so later reads recover it.
                # Also accepts a list of 2-tuples: dict([(k, v), ...]).
                if e.args:
                    t = A.expr_type(e.args[0])
                    if t not in ("dict", "any", "list", "tuple"):
                        raise SemaError("dict() requires a dict or list-of-pairs argument", e.pos, ErrorCode.E_ARG_TYPE)
                    if t == "dict":
                        e.value_type = self._dict_value_type(e.args[0], scope)
                    else:
                        e.dict_from_pairs = True
                return
            if e.func == "divmod":
                # divmod(a, b) -> (a // b, a % b), floor semantics. FLOAT
                # arguments are valid Python and give float results
                # (`divmod(7.5, 2)` is `(3.0, 1.5)`) -- rejecting them made a
                # correct program a compile error.
                _dm_float = False
                for a in e.args:
                    t = A.expr_type(a)
                    if t == "float":
                        _dm_float = True
                    elif t not in ("int", "any"):
                        raise SemaError(
                            "divmod() requires int or float arguments", e.pos,
                            ErrorCode.E_ARG_TYPE,
                        )
                if _dm_float:
                    e.divmod_float = True  # type: ignore[attr-defined]
                    e.tuple_elem_types = ["float", "float"]
                else:
                    e.tuple_elem_types = ["int", "int"]
                return
            # Argument-type sanity for builtins that care. An opaque ("any")
            # argument is accepted everywhere — we can't know its real type.
            if e.func == "len":
                t = A.expr_type(e.args[0])
                if t not in ("str", "list", "dict", "tuple", "set", "any", "int") and not t.startswith("instance:"):
                    # "int" is the default for unannotated vars — accept leniently
                    raise SemaError(
                        "len() requires a string, list, dict, tuple, or set", e.pos,
                        ErrorCode.E_ARG_TYPE,
                    )
            elif e.func == "int":
                t = A.expr_type(e.args[0])
                # An instance may define __int__ — accepted leniently like
                # str()'s __str__/__repr__ dispatch; codegen falls back to
                # treating the pointer as a raw int if no __int__ exists.
                if t not in ("str", "float", "int", "any") and not t.startswith(
                    "instance:"
                ):
                    raise SemaError("int() requires str / float / int", e.pos, ErrorCode.E_ARG_TYPE)
            elif e.func == "float":
                t = A.expr_type(e.args[0])
                # An instance defining `__float__` converts through it, exactly
                # as `int()` already accepts `__int__`. Rejecting it outright
                # made `float(obj)` a compile error for a class that implements
                # the conversion CPython asks for.
                if t not in ("str", "int", "float", "any") and not (
                    t.startswith("instance:")
                    and self._resolve_method(t.split(":", 1)[1], "__float__")
                    is not None
                ):
                    raise SemaError("float() requires str / int / float", e.pos, ErrorCode.E_ARG_TYPE)
            elif e.func == "str":
                t = A.expr_type(e.args[0])
                # int/float/str convert directly; list/tuple/dict/set stringify
                # via their repr; an opaque value or an instance (which may define
                # __str__/__repr__) is accepted leniently. All yield a str.
                if t not in (
                    "int", "float", "str", "any", "list", "tuple", "dict", "set"
                ) and not t.startswith("instance:"):
                    raise SemaError(
                        "str() requires a scalar, container, or object", e.pos,
                        ErrorCode.E_ARG_TYPE,
                    )
            return
        if e.func in self.overload_sets:
            for a in e.args:
                self._check_expr(a, scope)
            sig = self._resolve_overload(e.func, self.overload_sets[e.func], e.args, e.pos)
            e.resolved_overload_symbol = _overload_symbol(e.func, sig)
            self._bind_args(
                e, sig.param_names, sig.param_defaults, sig.vararg, e.pos, e.func,
                kwarg=sig.kwarg,
            )
            if sig.ret_type is not None:
                ret_tuple_ov: tuple = sig.ret_type  # type: ignore
                e.inferred_type = ret_tuple_ov[0]
            else:
                e.inferred_type = "int"
            return
        if e.func in self.funcs:
            sig = self.funcs[e.func]
            self._expand_dstar_kwarg(e, sig.param_names, scope)
            # Plain positional calls keep the precise arity diagnostics; calls
            # with keyword args or to a `*args` function are validated by the
            # binder instead.
            if sig.vararg is None and sig.kwarg is None and not e.kwargs:
                required = sig.arity - sig.n_defaults
                if not (required <= len(e.args) <= sig.arity):
                    if required == sig.arity:
                        raise SemaError(
                            f"{e.func}() takes {sig.arity} argument(s), got {len(e.args)}",
                            e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                    raise SemaError(
                        f"{e.func}() takes {required}-{sig.arity} arguments, got {len(e.args)}",
                        e.pos,
                        ErrorCode.E_ARG_COUNT,
                    )
            # Normalize every call to a complete positional argument list
            # (defaults filled, keyword args placed, varargs packed) so codegen
            # always sees a fixed-shape call.
            self._bind_args(
                e, sig.param_names, sig.param_defaults, sig.vararg, e.pos, e.func,
                kwarg=sig.kwarg,
            )
            self._coerce_args_to_param_types(e, sig, scope)
            for a in e.args:
                self._check_expr(a, scope)
            # Return type priority: an inferred `return a, b` tuple shape wins
            # (it carries per-slot kinds); then an explicit return annotation;
            # otherwise int.
            if e.func in self.func_ret_tuple:
                e.inferred_type = "tuple"
                e.tuple_elem_types = list(self.func_ret_tuple[e.func])
            elif sig.ret_type is not None:
                # Explicit subscript reads, not `ty, el, _val = sig.ret_type`:
                # sig.ret_type is declared `ret_type: object` on FuncSig (see
                # its dataclass definition), and `object` isn't a concrete
                # type sema can track field-wise -- a tuple-unpack of it left
                # `ty` opaque instead of carrying the real "str"/"list"/etc.
                # string, so e.inferred_type ended up wrong for EVERY plain
                # function call whose return type came from this path. This
                # likely explains a wide swath of "A.something(...)"-style
                # cross-module call sites throughout sema.py/codegen.py
                # reading back "any" instead of their annotated return type.
                ret_tuple_val: tuple = sig.ret_type  # type: ignore
                ty: str = ret_tuple_val[0]
                el = ret_tuple_val[1]
                _val = ret_tuple_val[2]
                e.inferred_type = ty
                if sig.ret_bool:
                    e.is_bool = True
                if ty == "list" and el is not None:
                    e.list_el_type = el
                    if el == "tuple" and sig.ret_list_tuple_types:
                        e.tuple_elem_types = list(sig.ret_list_tuple_types)  # type: ignore
                    elif el in ("list", "dict") and sig.ret_inner_el_type:
                        # list[list[T]] / list[dict[K,V]]: carry the leaf kind
                        # so `for row in rows: row[i]` recovers T.
                        e.el_value_type = sig.ret_inner_el_type
                elif ty == "dict" and _val is not None:
                    # Carry the value kind so `d = f()[k]` / `f()[k].attr`
                    # reads recover it (bare `-> dict` gives value kind "any").
                    e.value_type = _val
            else:
                e.inferred_type = "int"
            return
        if e.func in self.ffi_funcs:
            fn = self.ffi_funcs[e.func]
            e.args = self._fold_variadic_ffi(fn, e, e.args)
            self._check_ffi_call(fn, e.args, e.pos, scope, label=e.func)
            _fn_ret2: str = getattr(fn, "ret_type", "int")
            e.inferred_type = _fn_ret2 if _fn_ret2 else "int"
            if getattr(fn, "ret_bool", False):
                e.is_bool = True  # type: ignore[attr-defined]
            self._apply_ffi_element_return(fn, e, scope)
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
                # Explicit ClassSig annotation, and a direct field read
                # instead of getattr(): cls_sig's unannotated type defaulted
                # wrong (the established self.classes[...] dict-subscript
                # opacity pattern), and getattr() on top of that doubled the
                # opacity -- cls_sig.parent is always a real field (str or
                # None) on ClassSig, never genuinely missing, so getattr()
                # was unnecessary and made the read worse, not safer.
                cls_sig: ClassSig = self.classes[e.func]
                parent: "str | None" = cls_sig.parent
                parent_is_external = parent is not None and parent not in self.classes
                if e.dstar is not None:
                    raise SemaError(
                        f"{e.func}() has no declared parameter list to expand "
                        "**expr against",
                        e.dstar.pos,
                        ErrorCode.E_DSTAR_NO_PARAM_LIST,
                    )
                if (
                    (e.args or e.kwargs)
                    and not cls_sig.fields
                    and not self._is_exception_class(e.func)
                    and not parent_is_external
                ):
                    raise SemaError(
                        f"{e.func}() has no __init__ and takes no arguments",
                        e.pos,
                        ErrorCode.E_ARG_COUNT,
                    )
                for a in e.args:
                    self._check_expr(a, scope)
                for _kn, kv in e.kwargs:
                    self._check_expr(kv, scope)
                e.inferred_type = f"instance:{e.func}"
                return
            else:
                # Explicit subscript read, not `_, sig = init`: init's static
                # type is the generic "tuple" (Optional[tuple[str, FuncSig]]
                # isn't tracked element-by-element), so a tuple-unpack left
                # sig opaque ("any") instead of carrying FuncSig's real
                # fields. sig.param_names[1:] / sig.param_defaults[1:] then
                # read as opaque "any" slices, and _gen_subscript's slice
                # dispatch (which only routes to the list-slice path when the
                # object's static type is exactly "list") fell through to
                # the STRING-slice path instead, corrupting the list passed
                # into _bind_args and crashing inside dict/list runtime
                # helpers down the line whenever a constructor call used a
                # keyword argument.
                sig: FuncSig = init[1]
                self._expand_dstar_kwarg(e, sig.param_names[1:], scope)
                expected = sig.arity - 1
                if sig.vararg is None and sig.kwarg is None and not e.kwargs:
                    required = expected - sig.n_defaults
                    if not (required <= len(e.args) <= expected):
                        if required == expected:
                            raise SemaError(
                                f"{e.func}() takes {expected} argument(s), got {len(e.args)}",
                                e.pos,
                                ErrorCode.E_ARG_COUNT,
                            )
                        raise SemaError(
                            f"{e.func}() takes {required}-{expected} arguments, got {len(e.args)}",
                            e.pos,
                            ErrorCode.E_ARG_COUNT,
                        )
                sig_param_names: list = sig.param_names
                sig_param_defaults: list = sig.param_defaults
                self._bind_args(
                    e,
                    sig_param_names[1:],
                    sig_param_defaults[1:],
                    sig.vararg,
                    e.pos,
                    e.func,
                    kwarg=sig.kwarg,
                )
                # `self` occupies slot 0 of the signature but not of the call.
                self._coerce_args_to_param_types(e, sig, scope, skip=1)
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
        if e.func in scope.types:
            # A name bound to a user instance with __call__: dispatch to the
            # method. Normalize args against __call__'s sig (skip self).
            _inst_t = scope.types.get(e.func, "")
            if _inst_t.startswith("instance:"):
                _cls = _inst_t.split(":", 1)[1]
                _call_resolved = self._resolve_method(_cls, "__call__")
                if _call_resolved is not None:
                    # Explicit subscript, not `_owner, _sig = _call_resolved`:
                    # same opaque-tuple-unpack issue as the constructor call
                    # site above.
                    _owner: str = _call_resolved[0]
                    _sig: FuncSig = _call_resolved[1]
                    _sig_param_names: list = _sig.param_names
                    _sig_param_defaults: list = _sig.param_defaults
                    self._expand_dstar_kwarg(e, _sig_param_names[1:], scope)
                    self._bind_args(
                        e,
                        _sig_param_names[1:],
                        _sig_param_defaults[1:],
                        _sig.vararg,
                        e.pos,
                        f"{_cls}.__call__",
                        kwarg=_sig.kwarg,
                    )
                    for _a in e.args:
                        self._check_expr(_a, scope)
                    e.dunder_call_owner = _owner  # type: ignore
                    if _sig.ret_type is not None:
                        _ty, _el, _val = _sig.ret_type  # type: ignore
                        e.inferred_type = _ty
                        if _ty == "list" and _el is not None:
                            e.list_el_type = _el  # type: ignore
                    else:
                        e.inferred_type = "any"
                    return
            if e.dstar is not None:
                # `target(**kwargs)` where `target` is an opaque callable
                # VALUE (a parameter/variable bound to something callable,
                # not a statically-resolvable function/class/instance-with-
                # __call__). There's no compile-time parameter list to expand
                # each `**kwargs` key onto a fixed slot, but the dict already
                # exists as a real runtime value -- so pass it straight
                # through as the call's trailing dict-typed argument, exactly
                # the ABI position `_bind_args` would place a `**kwargs`
                # param's packed DictLit. The callee is expected to accept a
                # `**kwargs` parameter (asmpython already supports declaring
                # and receiving one -- see `_bind_args`'s `kwarg` handling);
                # this is the missing OTHER half, the dynamic call site.
                # Marked with `dstar_dynamic` so ir_lower emits the indirect
                # call with the dict appended rather than trying to expand it.
                self._check_expr(e.dstar, scope)
                _dstar_t = A.expr_type(e.dstar)
                if _dstar_t not in ("dict", "any"):
                    raise SemaError(
                        "**expr call argument must be a dict",
                        e.dstar.pos,
                        ErrorCode.E_DSTAR_NOT_DICT,
                    )
                for _kn, _kv in e.kwargs:
                    self._check_expr(_kv, scope)
                for a in e.args:
                    self._check_expr(a, scope)
                e.dstar_dynamic = True  # type: ignore
                e.inferred_type = "any"
                return
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
            elif (
                scope.types.get(e.func) in ("any", "closure")
                or e.func[:1].isupper()
            ):
                # A "closure"-typed name is an opaque callable like any other
                # here: its result kind is unknown, not int. Before closure
                # factories were detected up front this name typed "any" and
                # landed in this same branch; keeping "closure" with it
                # preserves that, rather than falling through to the int
                # default and printing the result as a number.
                e.inferred_type = "any"
            else:
                e.inferred_type = "int"
            return
        raise SemaError(f"undefined function {e.func!r}", e.pos, ErrorCode.E_UNDEFINED_FUNC)


def _walk_call_sites(statements: list):
    """Yield every `A.Call` node reachable from `statements`, at any nesting
    depth (expressions inside expressions, statements inside statements).

    A minimal, self-contained walker deliberately NOT shared with the
    `*_compat_fixes.py` modules' own walkers: those import `SemaAnalyzer` from
    this module, so importing one of them back here would be circular. Only
    the child-attribute names actually needed to reach every `A.Call` in
    practice are covered (mirrors the coverage of e.g.
    `object_flow_compat_fixes._walk_expression`/`_walk_statements`)."""
    def walk_expr(e):
        if e is None:
            return
        if isinstance(e, A.Call):
            yield e
        for name in ("left", "right", "operand", "obj", "index", "test", "func_expr",
                     "body", "orelse", "value", "elt", "key", "iter"):
            child = getattr(e, name, None)
            if child is not None and not isinstance(child, (list, str)):
                yield from walk_expr(child)
        for name in ("args", "elems", "operands", "values", "keys",
                     "segments", "extra_for_iters", "extra_for_conds"):
            children = getattr(e, name, None)
            if isinstance(children, list):
                for child in children:
                    yield from walk_expr(child)
        kwargs = getattr(e, "kwargs", None)
        if isinstance(kwargs, list):
            for _name, child in kwargs:
                yield from walk_expr(child)

    for s in statements:
        for name in ("expr", "value", "test", "iter", "target", "obj"):
            expr = getattr(s, name, None)
            if expr is not None and not isinstance(expr, str):
                yield from walk_expr(expr)
        values = getattr(s, "values", None)
        if isinstance(values, list) and not isinstance(s, (A.ListLit, A.TupleLit, A.SetLit)):
            for expr in values:
                if expr is not None and not isinstance(expr, str):
                    yield from walk_expr(expr)
        for name in ("then", "orelse", "body", "handler", "else_body", "finally_body"):
            nested = getattr(s, name, None)
            if isinstance(nested, list):
                yield from _walk_call_sites(nested)
        if isinstance(s, A.Try):
            for _types, _binding, body in s.extra_handlers:
                yield from _walk_call_sites(body)


def _resolve_class_aliases(mod: A.Module) -> None:
    """Rewrite `Alias(...)` call sites to `RealClass(...)` wherever a module-
    level statement unconditionally binds `Alias = RealClass` (transitively:
    `B = A; C = B` both resolve to `A`).

    Runs BEFORE `SemaAnalyzer` is constructed -- i.e. before any of the
    `*_compat_fixes.py` layers wrapping `SemaAnalyzer.analyze` see the module
    at all -- because several of those layers independently re-derive their
    own reachability/type information straight from the raw AST's `A.Call`
    nodes (e.g. `live_definition_compat_fixes`'s "is this class ever
    constructed" reachability scan checks `call.func in class_names`
    directly). Resolving the alias only inside `SemaAnalyzer._check_call`
    left every earlier layer still seeing the alias name, so a class
    constructed exclusively through an alias looked unreachable to them and
    got its methods replaced with inert stubs -- while sema itself compiled
    the call correctly. Rewriting the AST once, up front, is the only fix
    that reaches every layer uniformly instead of teaching each one alias
    resolution individually.

    asmpython has no runtime class objects: a bare class name used as a value
    loads its RTTI id (a plain int), so `Alias(...)` through a name bound
    this way previously called through that integer id and crashed. Only
    UNCONDITIONAL, single module-level assignment is trusted as an alias -- a
    name reassigned more than once, or assigned inside a function/method/
    conditional, isn't a stable compile-time alias for a class the way an
    import alias is, so it's left alone (a genuinely dynamic rebinding stays
    opaque and is rejected at the call site like any other call on a
    non-class value, rather than silently picking one arm)."""
    class_names = {c.name for c in mod.classes}
    assigned_once: dict[str, str] = {}
    reassigned: set[str] = set()
    for s in mod.body:
        if isinstance(s, A.Assign) and isinstance(s.value, A.Name):
            if s.target in assigned_once or s.target in reassigned:
                reassigned.add(s.target)
                assigned_once.pop(s.target, None)
            else:
                assigned_once[s.target] = s.value.name
        elif isinstance(s, A.Assign):
            reassigned.add(s.target)
            assigned_once.pop(s.target, None)

    aliases: dict[str, str] = {}
    for alias, target in assigned_once.items():
        seen: set[str] = {alias}
        current = target
        while current in assigned_once and current not in seen:
            seen.add(current)
            current = assigned_once[current]
        if current in class_names:
            aliases[alias] = current
    if not aliases:
        return

    def rewrite(call: A.Call) -> None:
        if call.func in aliases and call.func not in class_names:
            call.func = aliases[call.func]

    for call in _walk_call_sites(mod.body):
        rewrite(call)
    for f in mod.funcs:
        for call in _walk_call_sites(f.body):
            rewrite(call)
    for c in mod.classes:
        for m in c.methods:
            for call in _walk_call_sites(m.body):
                rewrite(call)


def _class_key_name(key: "A.Expr | None") -> "str | None":
    """If `key` is a class-OBJECT dict key -- a bare class reference used as a
    key, either `ClassName` (A.Name) or `module.ClassName` (A.Attr, e.g.
    `ast.Add`) -- return the leaf class name (`"Add"`); else None.

    Deliberately excludes anything that is already a supported key: string and
    int literals, and `**spread` entries (a None key). Only a Name/Attr whose
    referenced identifier looks like a class (leading uppercase letter) counts,
    which is exactly the `{ast.Add: ..., ast.Sub: ...}` shape asmpython's
    string-only dict runtime can't store directly."""
    if isinstance(key, A.Name):
        return key.name if key.name[:1].isupper() else None
    if isinstance(key, A.Attr) and isinstance(key.obj, (A.Name, A.Attr)):
        return key.name if key.name[:1].isupper() else None
    return None


def _is_type_call(e: "A.Expr | None") -> bool:
    """`type(x)` -- a one-argument call to the `type` builtin."""
    return isinstance(e, A.Call) and e.func == "type" and len(e.args) == 1


def _rewrite_class_keyed_dicts(mod: A.Module) -> None:
    """Support constant dicts keyed by CLASS OBJECTS, looked up via `type(x)`.

    asmpython's dict runtime stores string keys only (keys are strdup'd and
    hashed as C strings -- see codegen.py's `_runtime_dict_set`). A dict
    literal like `_BINARY_OPS = {ast.Add: Op.BINARY_ADD, ast.Sub: ...}`, read
    as `_BINARY_OPS[type(node.op)]` / tested as `type(node.op) in
    _BINARY_OPS`, therefore can't be compiled directly.

    But a class object's identity is fully captured by its (unique) NAME here,
    and asmpython already supports `type(x).__name__` (a str) and string-keyed
    dicts. So rewrite the whole pattern to its string-keyed equivalent, up
    front, before sema/codegen ever see the unsupported key type:
      * every class-object key `ast.Add` -> the string literal `"Add"`
      * every `D[type(x)]`            -> `D[type(x).__name__]`
      * every `type(x) in/not in D`   -> `type(x).__name__ in/not in D`

    Only dicts whose keys are ALL class-object references are touched (a
    genuinely str/int-keyed dict is left exactly as-is), and only the two
    lookup spellings above are rewritten. Runs as a module-level pre-pass
    (like `_resolve_class_aliases`) so it reaches every downstream layer
    uniformly. Pure AST-to-AST: no runtime, codegen, or backend changes, so
    it works identically across every target/backend."""
    class_keyed: set[str] = set()

    def dict_is_class_keyed(d: A.DictLit) -> bool:
        if not d.keys:
            return False
        saw_class_key = False
        for k in d.keys:
            if k is None:
                return False  # a **spread entry -- not a plain class-keyed dict
            name = _class_key_name(k)
            if name is None:
                return False
            saw_class_key = True
        return saw_class_key

    # Pass 1: find module-level `NAME = {ClassRef: v, ...}` globals and rewrite
    # their keys to string literals. Record the global names so their lookup
    # sites can be rewritten to match.
    for s in mod.body:
        if (
            isinstance(s, A.Assign)
            and isinstance(s.target, str)
            and isinstance(s.value, A.DictLit)
            and dict_is_class_keyed(s.value)
        ):
            d = s.value
            d.keys = [
                A.StrLit(value=_class_key_name(k), pos=getattr(k, "pos", d.pos))
                for k in d.keys
            ]
            class_keyed.add(s.target)

    if not class_keyed:
        return

    def rewrite_expr(e):
        """Recursively rewrite `D[type(x)]` and `type(x) (not) in D` lookups
        against a known class-keyed dict `D`. Returns the (possibly new) node
        so callers can rebind the field they read it from."""
        if e is None or isinstance(e, str):
            return e
        if isinstance(e, A.Subscript):
            e.obj = rewrite_expr(e.obj)
            e.index = rewrite_expr(e.index)
            if (
                isinstance(e.obj, A.Name)
                and e.obj.name in class_keyed
                and _is_type_call(e.index)
            ):
                e.index = A.Attr(obj=e.index, name="__name__", pos=e.index.pos)
            return e
        if isinstance(e, A.Compare):
            e.operands = [rewrite_expr(o) for o in e.operands]
            for i, op in enumerate(e.ops):
                if op in ("in", "not in"):
                    needle = e.operands[i]
                    haystack = e.operands[i + 1]
                    if (
                        isinstance(haystack, A.Name)
                        and haystack.name in class_keyed
                        and _is_type_call(needle)
                    ):
                        e.operands[i] = A.Attr(
                            obj=needle, name="__name__", pos=needle.pos
                        )
            return e
        # Generic recursion into every child expression field so a lookup
        # nested anywhere (a call argument, an f-string segment, a ternary
        # arm, ...) is still reached.
        for name in ("left", "right", "operand", "obj", "index", "test", "func_expr",
                     "body", "orelse", "value", "elt", "key", "iter", "cond"):
            child = getattr(e, name, None)
            if child is not None and not isinstance(child, (list, str)):
                setattr(e, name, rewrite_expr(child))
        for name in ("args", "elems", "operands", "values", "keys",
                     "segments", "extra_for_iters", "extra_for_conds"):
            children = getattr(e, name, None)
            if isinstance(children, list):
                for i, child in enumerate(children):
                    if child is not None and not isinstance(child, str):
                        children[i] = rewrite_expr(child)
        kwargs = getattr(e, "kwargs", None)
        if isinstance(kwargs, list):
            for i, pair in enumerate(kwargs):
                kname, child = pair
                kwargs[i] = (kname, rewrite_expr(child))
        return e

    def rewrite_stmts(stmts: list) -> None:
        for s in stmts:
            for name in ("expr", "value", "test", "iter", "target", "obj",
                         "index", "cond"):
                child = getattr(s, name, None)
                if child is not None and not isinstance(child, str):
                    setattr(s, name, rewrite_expr(child))
            values = getattr(s, "values", None)
            if isinstance(values, list) and not isinstance(
                s, (A.ListLit, A.TupleLit, A.SetLit)
            ):
                for i, child in enumerate(values):
                    if child is not None and not isinstance(child, str):
                        values[i] = rewrite_expr(child)
            for name in ("then", "orelse", "body", "handler", "else_body",
                         "finally_body"):
                nested = getattr(s, name, None)
                if isinstance(nested, list):
                    rewrite_stmts(nested)
            if isinstance(s, A.Try):
                for _types, _binding, body in s.extra_handlers:
                    rewrite_stmts(body)

    rewrite_stmts(mod.body)
    for f in mod.funcs:
        rewrite_stmts(f.body)
    for c in mod.classes:
        for m in c.methods:
            rewrite_stmts(m.body)


def analyze(
    mod: A.Module,
    *,
    source_dir=None,
    collect_errors: bool = False,
    active_extensions: "frozenset[str] | None" = None,
) -> None:
    """Run semantic analysis over `mod`.

    `source_dir` is the directory of the source file (a Path or None). Used for
    resolving relative imports and project-local module references. None for
    isolation (used by the self-host gauntlet and tests).

    When `collect_errors` is True, sema continues past the first error and
    raises `MultiSemaError` at the end with every diagnostic collected.

    `active_extensions` is the same --ext activation set the parser already
    received, needed here too since several compiler extensions (unlike
    `constants`) are enforced at the semantic-analysis level rather than
    via new syntax alone.
    """
    _resolve_class_aliases(mod)
    _rewrite_class_keyed_dicts(mod)
    SemaAnalyzer(
        mod,
        source_dir=source_dir,
        collect_errors=collect_errors,
        active_extensions=active_extensions,
    ).analyze()
