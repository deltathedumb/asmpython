"""Lowering for a function whose values are runtime objects.

A SEPARATE set of methods from the statically typed ones in `lower.py`, not a
flag threaded through them. The two share their control-flow shape and nothing
else: here every value is one opaque pointer and every operation is a call into
`objects/csource.py`, where the value carries its own kind.

Keeping them apart is the point. A single `_expr` that returned an `i64` in one
mode and a pointer in another is precisely the shape that makes a value's
representation follow the slot it is stored in rather than the value -- the
defect the conformance suite's TAXONOMY.md names as the dominant one in the
compiler this replaces.

THE BOUNDARY between the two is `_dyn_call`, and it is one place on purpose: a
dynamic function calling a statically typed one unwraps each argument to the
machine type it declared and wraps the result back. Nothing else crosses.
"""
from __future__ import annotations

import ast

from ...ir import types as T
from ...ir import Builder, Function
from ...ir.module import Global, Instruction, Linkage
from ...ir.opcodes import Op
from .analysis import (
    _VALUE_BUILTINS,
    BOOL, CELL, ENTRY_NAME, FLOAT, FREE, INT, MODULE_DUNDERS, NONE, OBJ,
    OBJECT_DEFAULTS,
    SemType, TO_IR,
    _EXC_NAMES, _handler_names, _target_names, int_literal,
    sem_type, span_of,
)
from .methods import DICT_PARTS, method_symbol
from .modules import member, resolve

#: Python operator -> the runtime call that implements it.
DYN_BINOP = {
    ast.Add: "apy_add", ast.Sub: "apy_sub", ast.Mult: "apy_mul",
    ast.Div: "apy_truediv", ast.FloorDiv: "apy_floordiv", ast.Mod: "apy_mod",
    ast.Pow: "apy_pow", ast.BitAnd: "apy_bitand", ast.BitOr: "apy_bitor",
    ast.BitXor: "apy_bitxor", ast.LShift: "apy_lshift",
    ast.RShift: "apy_rshift",
    # PEP 465. No built-in kind implements it; it is a spelling for
    # libraries, and reaches `__matmul__` or nothing.
    ast.MatMult: "apy_matmul",
}
DYN_CMP = {
    ast.Eq: "apy_eq", ast.NotEq: "apy_ne", ast.Lt: "apy_lt",
    ast.LtE: "apy_le", ast.Gt: "apy_gt", ast.GtE: "apy_ge",
    ast.Is: "apy_is",
}


class _Synthetic:
    """The `info` a synthesised body is lowered under.

    IT EXISTS FOR ONE FIELD. `_dyn_check` asks whether the function being
    lowered is the ENTRY: inside the entry a failed operation stops the
    process, and inside anything else it returns failure for the caller to
    check. A thunk was lowered with whatever `info` the last real function
    left behind -- so a thunk emitted after the entry inherited "stop the
    process", and `int("x")` through a builtin held as a VALUE killed the
    program instead of raising a ValueError the caller could catch.

    Silent for as long as nobody passed a failing builtin as a value.
    """

    __slots__ = ("name", "ret", "dynamic", "locals", "params", "maybe_unbound")

    def __init__(self, name):
        self.name = name
        self.ret = OBJ
        self.dynamic = True
        self.locals = {}
        self.params = []
        self.maybe_unbound = set()


def _suspends(node) -> bool:
    """Whether evaluating this can SUSPEND the frame it is written in.

    `await` is the obvious one and `yield` is the same act: both compile to a
    RETURN out of the step function, and a register does not survive either.
    Asking only about `await` left every `f(x, (yield))` producing invalid IR
    -- and a generator that yields mid-expression is ordinary Python, not a
    corner.
    """
    if node is None:
        return False
    for n in ast.walk(node):
        if isinstance(n, (ast.Await, ast.Yield, ast.YieldFrom)):
            return True
        # `[x async for x in agen()]` SUSPENDS AND CONTAINS NO `await`. The
        # suspension is in the `async for` itself, so a walk looking for
        # `Await` nodes saw nothing and left the value being held across it in
        # a register -- `out.append([x async for x in agen()])` was refused as
        # invalid IR for that reason alone.
        if isinstance(n, ast.comprehension) and n.is_async:
            return True
    return False


DYN_UNARY = {ast.USub: "apy_neg", ast.UAdd: "apy_pos",
             ast.Invert: "apy_invert"}
#: Builtins that are one call on one argument.
DYN_UNARY_BUILTIN = {
    "int": "apy_to_int", "float": "apy_to_float", "bool": "apy_to_bool",
    "str": "apy_str", "repr": "apy_repr", "len": "apy_len",
    # `type(x)` answers a TYPE OBJECT, not its name. The name was a str,
    # so `type(a) is type(b)` compared two separately-built strings and
    # was False for two ints -- and `print(type(x))` said `int` where
    # CPython says `<class 'int'>`. The objects are interned by kind.
    "type": "apy_type_object", "sorted": "apy_sorted", "min": "apy_min",
    "max": "apy_max", "sum": "apy_sum", "reversed": "apy_reversed",
    "abs": "apy_abs", "round": "apy_round",
}

#: `obj.method(args)` -> (runtime symbol, argument count). The receiver is
#: passed first. A method whose shape is fixed costs one line here; anything
#: needing a default (`pop`, `get`) is spelled out in `_dyn_method` instead.
DYN_METHOD = {
    ("append", 1): "apy_seq_push",
    ("index", 1): "apy_index_of",
    ("count", 1): "apy_count_of",
    ("remove", 1): "apy_list_remove",
}


def _is_type_call(node) -> bool:
    """Is this the `type(x)` of a `type(x).__name__` pair?"""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "type" and len(node.args) == 1)


#: Builtins that are exactly one runtime call with the arguments in order.
#: `ascii` IS `repr` here: the two differ only for non-ASCII text, which this
#: runtime does not represent, and a second entry point would be the same code
#: under another name.
#: Builtin TYPE names whose methods can be taken unbound -- `str.lower`.
#: Only the kinds with methods in the table; `int.bit_length` and the like
#: would need the same entry and are not reachable yet.
_BUILTIN_TYPES = frozenset({"str", "bytes", "list", "dict", "set",
                            "frozenset", "tuple"})

_DIRECT_BUILTINS = {
    "ord": "apy_ord", "chr": "apy_chr", "id": "apy_id",
    "__import__": "apy_import",
    "bin": "apy_bin", "hex": "apy_hex", "oct": "apy_oct",
    # NOT an alias for `repr`: `ascii` escapes every non-ASCII character,
    # which is the whole point of it.
    "ascii": "apy_ascii",
    "hash": "apy_hash", "callable": "apy_callable",
    "all": "apy_all", "any": "apy_any", "divmod": "apy_divmod",
    "hasattr": "apy_hasattr", "getattr": "apy_getattr", "dir": "apy_dir",
    "ExceptionGroup": "apy_excgroup_new",
    "BaseExceptionGroup": "apy_excgroup_new",
    "iter": "apy_iter", "issubclass": "apy_is_subclass",
    "vars": "apy_vars", "setattr": "apy_setattr", "delattr": "apy_delattr",
    "map": "apy_map", "filter": "apy_filter",
}

#: Builtins whose SHAPE depends on how they were called -- `round(x)` against
#: `round(x, n)`, `min(xs)` against `min(a, b)`. Listed so the dispatcher only
#: has to look at the calls that might be one of these; see
#: `_dyn_multi_builtin` for why the count and not the runtime decides.
#: `Type.name(...)` where the attribute is NOT an unbound method -- there is
#: no receiver of that type to be the first argument. `str.lower` is the other
#: shape and goes through `_dyn_unbound_method`; these are constructors, and
#: without them the type name resolved to the builtin's callable thunk and the
#: attribute came off a function.
_TYPE_STATICS = {
    ("dict", "fromkeys"): ("apy_dict_fromkeys", 2, ("apy_none",)),
    ("int", "from_bytes"): ("apy_from_bytes_n", 2, ()),
    ("bytes", "fromhex"): ("apy_bytes_fromhex", 1, ()),
    # Three arguments always: the two- and three-argument forms differ only
    # in whether anything is deleted, and None says "nothing" without the
    # table needing to carry two arities.
    ("str", "maketrans"): ("apy_str_maketrans", 3, ()),
    ("float", "fromhex"): ("apy_float_fromhex", 1, ()),
}

_MULTI_BUILTINS = frozenset({"round", "int", "sum", "min", "max", "zip",
                             "iter", "pow", "format", "getattr",
                             # The three runtime descriptors. They share one
                             # constructor and differ only by a constant, so
                             # they are lowered here rather than given three
                             # symbols -- see `apy_descr_new`.
                             "property", "classmethod", "staticmethod",
                             "slice"})

#: The builtins with no fixed argument count, and the runtime call that takes
#: their arguments as a TUPLE. A value-form has no compile-time count, so its
#: thunk declares `*rest` and hands the tuple straight over -- which is what
#: makes `print` and `dict` usable as values at all.
#: `property`, `classmethod`, `staticmethod` -> the constant `apy_descr_new`
#: takes. Mirrors the enum in objects/csource.py; a value here that the runtime
#: does not know would make every read through the descriptor answer wrongly
#: rather than fail, so the two are written to be read side by side.
#: Methods that answer a str even when the receiver is BYTES -- they convert
#: rather than mirror, so `apy_str_like` must leave their result alone.
_BYTES_TO_TEXT = frozenset({"hex", "decode"})

_DESCRIPTOR_KINDS = {"property": 0, "classmethod": 1, "staticmethod": 2}

#: Builtin names that are TYPES as well as callables. Used as a value each is
#: a thunk -- that is what makes `map(str, xs)` work -- and each is also a
#: class, which is what `print(int)` and `isinstance(x, t)` ask about.
#: Only the names that HAVE a value form -- see `_VALUE_BUILTINS` in
#: analysis. `type` and `object` are not among them, and listing them here
#: would have claimed a flag for a value that cannot be produced.
def _hostsvc_names():
    """The host-service names, minus the platform floor.

    THE FLOOR IS EXCLUDED because it is reachable from the static path only
    and has always been: `plat_write` from dynamic code would need the same
    boxing, and nothing asks for it. Widening that is a separate decision.
    """
    from ...objects import hostsvc as _hs
    return [n for n in _hs.ALL if _hs.GROUP_OF[n] not in _hs.MANDATORY]


#: Every host service, for recognising one at a dynamic call site. Read from
#: `objects/hostsvc.py` rather than listed here, because a hand-kept second copy
#: of a name list is the thing that has drifted in this project three times.
_HOSTSVC_NAMES = frozenset(_hostsvc_names())

_BUILTIN_TYPE_VALUES = frozenset({
    "int", "float", "bool", "str", "bytes", "list", "tuple", "dict", "set",
    "frozenset"})

#: The two builtin CLASSES that are values in their own right. `object` is
#: what `object.__new__(cls)` and `class C(object)` name, and `type` is a
#: metaclass's base; neither is a kind the way `int` is, so neither can travel
#: as text the way `_BUILTIN_TYPE_VALUES` do.
_CLASS_BUILTINS = {"object": "apy_object_class", "type": "apy_type_class"}

#: The builtins whose one argument is OPTIONAL, and what they answer with
#: none. `defaultdict(list)` calls the value form with nothing, and a thunk
#: that declared a required parameter reported an arity error for a call
#: CPython answers with the type's zero value.
_EMPTY_DEFAULTS = {
    "list": "apy_list_new", "tuple": "apy_tuple_new", "str": "",
    "float": 0.0, "int": 0, "dict": "apy_dict_new", "set": "apy_set_new",
    "frozenset": "apy_frozenset_new", "bytes": "", "bool": False,
}

#: The runtime kind number for each builtin a class may extend. These are
#: the values of the C's kind enum, and the two lists must agree -- a wrong
#: number gives an instance the wrong kind of storage.
_BUILTIN_BASE_KIND = {"str": 4, "list": 5, "tuple": 6, "dict": 7, "set": 9}

_VARIADIC_THUNKS = {
    "print": "apy_print_seq", "dict": "apy_dict_of", "bytes": "apy_bytes_of",
}


class _TypeParams(ast.NodeTransformer):
    """Replace a type parameter's name with the object it stands for.

    PEP 695 puts `T` in scope for the alias's value and nowhere else, so
    binding it as a variable would leak it; substituting the already-lowered
    object into the expression keeps the scope exactly as wide as the value.
    """

    def __init__(self, held: dict) -> None:
        self.held = held

    def visit_Name(self, node: ast.Name):
        made = self.held.get(node.id)
        return _Given(made) if made is not None else node


class _Given(ast.expr):
    """An expression that has already been lowered.

    A thunk's body is `name(x)` where `x` is a register the caller was handed,
    not source text -- so the argument cannot be an ordinary AST node. This
    carries the register through `_dyn_expr` unchanged, which lets the thunk
    reuse the real builtin lowering rather than restating it.
    """

    _fields = ()

    def __init__(self, register: int) -> None:
        super().__init__()
        self.register = register


class DynamicLowering:
    """Mixed into `Lowerer`; uses its builder, module and symbol table."""

    # ── values ──────────────────────────────────────────────────────────────
    #: What a machine word holds. A literal outside it cannot be a `const`.
    _INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1

    def _dyn_int_literal(self, value: int) -> int:
        """An integer literal, whatever its size.

        Inside a machine word it is a constant, which is the common case and
        costs nothing. Outside one it travels as its DECIMAL TEXT and the
        runtime parses it -- because the IR's `const` is a machine word, and
        emitting `9223372036854775808` as one wrapped it to
        `-9223372036854775808`. That was every big-integer literal in the
        program silently becoming a different number, which arithmetic then
        carried everywhere: the runtime had arbitrary precision and the
        frontend never gave it a value to be precise about.
        """
        if self._INT64_MIN <= value <= self._INT64_MAX:
            return self.b.call(T.PTR, "apy_from_int",
                               [self.b.const(T.I64, value)])
        digits = str(abs(value))
        return self.b.call(
            T.PTR, "apy_int_literal",
            [self._dyn_text_addr(digits), self.b.const(T.I64, len(digits)),
             self.b.const(T.I64, 1 if value < 0 else 0)])

    def _dyn_bytes_literal(self, raw: bytes) -> int:
        """A bytes constant. Like a str literal, with the LENGTH passed.

        A str literal is NUL-terminated and its length measured at run time; a
        bytes literal cannot be, because `b"a\x00b"` is three bytes and
        measuring would truncate it to one. Carrying arbitrary octets is the
        whole point of the kind, so the length travels with the pointer.

        A trailing NUL is still written, so that anything treating the payload
        as a C string -- the repr helper does not, but `apy_str_take` shares a
        layout with str -- finds one.
        """
        name = self._bytes.get(raw)
        if name is None:
            name = f"__bytes{len(self._bytes)}"
            self._bytes[raw] = name
            self.module.globals.append(
                Global(name=name, size=len(raw) + 1, data=raw + b"\x00",
                       readonly=True))
        addr = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr, sym=name))
        return self.b.call(T.PTR, "apy_bytes_literal",
                           [addr, self.b.const(T.I64, len(raw))])

    @staticmethod
    def _mangled(attr: str, owner: str) -> str:
        """`__x` written inside `class C` IS `_C__x`. See `_mangle`."""
        if not attr.startswith("__") or attr.endswith("__"):
            return attr
        return "_" + owner.lstrip("_") + attr

    def _mangle(self, attr: str) -> str:
        """`self.__x` inside `class C` IS `self._C__x`.

        PRIVATE NAME MANGLING, and it is a compile-time textual rule rather
        than anything the runtime knows: two leading underscores and at most
        one trailing one, inside a class body, become `_ClassName__x` with the
        class's own leading underscores stripped. That is what makes a private
        attribute of a base class not collide with one of the same spelling in
        a subclass -- and it is why `hasattr(c, "__hidden")` is False for an
        object that plainly has one.

        Applied where the attribute NAME becomes text, which is every place it
        reaches the runtime.
        """
        if not attr.startswith("__") or attr.endswith("__"):
            return attr
        owner = self.info.owner if self.info is not None else None
        if owner is None:
            return attr
        return self._mangled(attr, self.classes[owner].name)

    def _dyn_attr_literal(self, attr: str) -> int:
        """An attribute name as a str value, mangled if it is private."""
        return self._dyn_str_literal(self._mangle(attr))

    def _dyn_str_literal(self, text: str) -> int:
        """A str constant: module data, plus the call that wraps it.

        Interned by content, so the same literal twice is one global. Not for
        space: `x is y` on two equal literals then answers True the way it does
        in CPython, and a program printing that comparison would otherwise
        depend on how many times the compiler happened to see the text.
        """
        raw = text.encode("utf-8") + b"\x00"
        name = self._strings.get(raw)
        if name is None:
            name = f"__str{len(self._strings)}"
            self._strings[raw] = name
            self.module.globals.append(
                Global(name=name, size=len(raw), data=raw, readonly=True))
        # THE LENGTH IS KNOWN HERE, so it is passed rather than re-derived.
        # `apy_from_cstr` finds the end with `strlen`, which TRUNCATES a
        # literal containing a NUL: `len("a\0b")` answered 1 where Python says
        # 3, and every operation downstream then worked on the wrong string.
        # The compiler has counted the bytes already; asking C to count them
        # again could only ever lose information.
        #
        # THE TRAILING NUL STAYS IN THE GLOBAL and out of the length. The rest
        # of the runtime reads `v.s.p` as a C string in 200-odd places
        # (`APY_CSTR`, `strcmp`, `snprintf`), so the terminator is load-bearing
        # even though it is not part of the value.
        return self.b.call(T.PTR, "apy_from_bytes",
                           [self._dyn_text_addr(text),
                            self.b.const(T.I64, len(raw) - 1)])

    def _dyn_text_addr(self, text: str) -> int:
        """The ADDRESS of an interned literal's bytes, without wrapping it.

        `_dyn_str_literal` builds a str value from this; a big integer literal
        wants the raw pointer, because the runtime parses the digits rather
        than holding them. Interning is shared, so the same text used both
        ways is one global.
        """
        raw = text.encode("utf-8") + b"\x00"
        name = self._strings.get(raw)
        if name is None:
            name = f"__str{len(self._strings)}"
            self._strings[raw] = name
            self.module.globals.append(
                Global(name=name, size=len(raw), data=raw, readonly=True))
        addr = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr, sym=name))
        return addr

    def _dyn_check(self) -> None:
        """Stop if the operation just emitted raised.

        A sticky flag checked after each fallible operation, not a longjmp.
        The runtime sets the flag and returns; where the check goes is the
        frontend's decision, and it has two answers:

        Inside a `try`, the check branches to the handler chain -- so an
        exception propagates by falling out of the ordinary control flow
        rather than by unwinding a stack the IR does not model.

        Outside one, in a FUNCTION, the function RETURNS -- with the flag
        still set and a null value, which is exactly what the runtime's own
        entry points do when they fail. The caller checks after the call and
        finds the error, so an exception leaves a function the same way it
        leaves an `apy_add`. Calling `apy_fatal_if_error` here instead killed
        the process at the raise: `def g(): raise ValueError` called inside a
        `try` in the caller never reached the handler, because there was no
        caller left to reach it.

        Outside one, in the ENTRY, it reports to stderr and exits 1, which is
        what an uncaught exception does. Deliberately not stdout: the suite
        diffs stdout, and a traceback there turns a correct failure into a
        wrong answer.
        """
        if not self.handlers:
            if self.info.name == ENTRY_NAME:
                self.b.call(T.VOID, "apy_fatal_if_error", [])
                return
            raised = self.b.call(T.I64, "apy_error_occurred", [])
            hit = self.b.cmp(Op.NE, T.I64, raised, self.b.const(T.I64, 0))
            failed = self.b.new_block("propagate")
            keep_going = self.b.new_block("ok")
            self.b.branch(hit, failed, keep_going)
            self.b.switch_to(failed)
            self._dyn_return_failure()
            self.b.switch_to(keep_going)
            return
        raised = self.b.call(T.I64, "apy_error_occurred", [])
        hit = self.b.cmp(Op.NE, T.I64, raised, self.b.const(T.I64, 0))
        keep_going = self.b.new_block("ok")
        self.b.branch(hit, self.handlers[-1], keep_going)
        self.b.switch_to(keep_going)

    def _dyn_bool_value(self, raw: int) -> int:
        """Wrap an i64 0/1 as a runtime bool."""
        return self.b.call(T.PTR, "apy_from_bool", [raw])

    def _dyn_not(self, value: int) -> int:
        truth = self.b.call(T.I64, "apy_truth", [value])
        flipped = self.b.cmp(Op.EQ, T.I64, truth, self.b.const(T.I64, 0))
        wide = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.EXTEND, T.I64, dst=wide, args=[flipped]))
        return self._dyn_bool_value(wide)

    # ── expressions ─────────────────────────────────────────────────────────
    def _dyn_expr(self, node) -> int:
        if isinstance(node, _Given):
            return node.register
        self.b.span = self._span(node)
        match node:
            case ast.NamedExpr(target=ast.Name(id=name)):
                value = self._dyn_expr(node.value)
                self._dyn_store(name, value)
                return value
            case ast.Constant(value=None):
                return self.b.call(T.PTR, "apy_none", [])
            case ast.Constant() if node.value is Ellipsis:
                # `...` -- a SINGLETON, so `... is Ellipsis` is True and a
                # fresh cell per literal would answer False.
                return self.b.call(T.PTR, "apy_ellipsis", [])
            case ast.Constant(value=bool() as v):
                return self.b.call(T.PTR, "apy_from_bool",
                                   [self.b.const(T.I64, int(v))])
            case ast.Constant(value=int() as v):
                return self._dyn_int_literal(v)
            case ast.Constant(value=float() as v):
                return self.b.call(T.PTR, "apy_from_float",
                                   [self.b.const(T.F64, v)])
            case ast.Constant(value=str() as v):
                return self._dyn_str_literal(v)
            case ast.Constant(value=complex() as v):
                # `2j` is a Constant whose value is a Python complex, so both
                # halves are known here and neither needs the runtime to
                # parse anything.
                return self.b.call(T.PTR, "apy_from_complex",
                                   [self.b.const(T.F64, v.real),
                                    self.b.const(T.F64, v.imag)])
            case ast.Constant(value=bytes() as v):
                return self._dyn_bytes_literal(v)
            case ast.Constant(value=None):
                return self.b.call(T.PTR, "apy_none", [])
            case ast.Attribute(value=ast.Name(id=base), attr=attr) if (
                    base in _BUILTIN_TYPES and base not in self.info.locals
                    and base not in self.infos
                    and base not in self.class_names
                    and method_symbol(attr, 0) is not None):
                # `str.lower` -- an UNBOUND method of a builtin type, which is
                # a value: `sorted(xs, key=str.lower)`. The thunk calls the
                # method on its argument, so `str.lower(x)` is `x.lower()`,
                # which is what an unbound method means.
                return self._dyn_unbound_method(base, attr)
            case ast.Lambda():
                # A lambda is a nested function written inline, and analysis
                # registered it as one. So this is the same function VALUE a
                # `def` statement produces -- closures, cells and defaults
                # included -- rather than a second mechanism.
                return self._dyn_function_value(self.def_keys[id(node)],
                                                "<lambda>")
            case ast.Name(id=name) if (name in _CLASS_BUILTINS
                                       and name not in self.info.locals
                                       and name not in self.infos
                                       and name not in self.class_names):
                # `object` AND `type` AS VALUES. `object.__new__(cls)` is what
                # a metaclass calls to make an instance without running the
                # class's own `__new__`, and both are ordinary class objects
                # the moment a program names one.
                return self.b.call(T.PTR, _CLASS_BUILTINS[name], [])
            case ast.Name(id=name) if (name not in self.info.locals
                                       and name in _VALUE_BUILTINS
                                       and name not in self.infos
                                       and name not in self.class_names):
                # `key=repr`, `map(str, xs)`. The builtin becomes a real
                # function VALUE, so everything that takes a callable takes it
                # without knowing it came from a builtin.
                return self._dyn_builtin_value(name)
            case ast.Name(id=name):
                return self._dyn_load(name)
            case ast.BinOp():
                held = self._spill_across_await(self._dyn_expr(node.left),
                                                node.right)
                right = self._dyn_expr(node.right)
                # READ BACK AFTER the right operand, not before: a load
                # emitted ahead of the suspension is a register that does not
                # survive it either.
                left = held()
                out = self.b.call(T.PTR, DYN_BINOP[type(node.op)],
                                  [left, right])
                self._dyn_check()
                return out
            case ast.UnaryOp(op=ast.Not()):
                return self._dyn_not(self._dyn_expr(node.operand))
            case ast.UnaryOp():
                out = self.b.call(T.PTR, DYN_UNARY[type(node.op)],
                                  [self._dyn_expr(node.operand)])
                self._dyn_check()
                return out
            case ast.BoolOp():
                return self._dyn_boolop(node)
            case ast.Compare():
                return self._dyn_compare(node)
            case ast.IfExp():
                return self._dyn_ifexp(node)
            case ast.Call():
                return self._dyn_call(node)
            case ast.List(elts=elts) | ast.Tuple(elts=elts):
                return self._dyn_sequence(node, elts)
            case ast.GeneratorExp():
                # A GENERATOR, not a list. Analysis registered the synthetic
                # `def` -- see `genexp_def` -- so this builds that function's
                # value and calls it with the outermost iterable, which is
                # what makes only that one eager.
                made = self._dyn_function_value(self.def_keys[id(node)],
                                                "<genexp>")
                source = self._dyn_expr(node.generators[0].iter)
                return self._dyn_indirect(made, [source])
            case ast.ListComp():
                return self._comprehension(node, "apy_list_new")
            case ast.SetComp():
                # A set is filled with `apy_set_push`, which DEDUPLICATES;
                # `apy_seq_push` would append and give `{2, 4, 4}`.
                return self._comprehension(node, "apy_set_new",
                                           "apy_set_push")
            case ast.DictComp():
                return self._comprehension(node, "apy_dict_new")
            case ast.Yield():
                return self._dyn_yield(node)
            case ast.YieldFrom():
                return self._dyn_yield_from(node)
            case ast.Await():
                return self._dyn_await(node)
            case ast.JoinedStr(values=parts):
                return self._dyn_fstring(parts)
            case ast.TemplateStr(values=parts):
                return self._dyn_template(parts)
            case ast.Set(elts=elts):
                held = self._held_accumulator(
                    self.b.call(T.PTR, "apy_set_new",
                                [self.b.const(T.I64, max(1, len(elts)))]),
                    elts)
                for element in elts:
                    # THE ELEMENT FIRST, the accumulator after: an element
                    # that suspends leaves a register the resume never wrote.
                    if isinstance(element, ast.Starred):
                        more = self._dyn_expr(element.value)
                        self.b.call(T.PTR, "apy_set_update", [held(), more])
                    else:
                        item = self._dyn_expr(element)
                        self.b.call(T.PTR, "apy_set_push", [held(), item])
                    self._dyn_check()
                return held()
            case ast.Dict(keys=keys, values=values):
                holder = self._held_accumulator(
                    self.b.call(T.PTR, "apy_dict_new",
                                [self.b.const(T.I64, max(1, len(keys)))]),
                    list(keys) + list(values))
                for k, v in zip(keys, values):
                    if k is None:
                        # `{**other}` -- SPREAD IN PLACE, at the position it
                        # was written, so a later key overwrites what the
                        # spread brought and an earlier one is overwritten
                        # by it. Order is the whole meaning here.
                        spread = self._dyn_expr(v)
                        self.b.call(T.PTR, "apy_update", [holder(), spread])
                        self._dyn_check()
                        continue
                    # Key then value, in source order: a display evaluates
                    # left to right and a later duplicate key overwrites.
                    # BOTH BEFORE the accumulator is read -- either may
                    # suspend, and a register does not survive that.
                    # The KEY must survive the VALUE's suspension too -- it
                    # is computed first and used after, which is exactly the
                    # shape `_spill_across_await` exists for.
                    key = self._spill_across_await(self._dyn_expr(k), v)
                    val = self._dyn_expr(v)
                    self.b.call(T.PTR, "apy_dict_set",
                                [holder(), key(), val])
                    self._dyn_check()
                return holder()
            case ast.Slice():
                # A SLICE AS A VALUE, which is what `c[1:2, 3]` needs: the
                # subscript is a tuple and one of its elements is this. The
                # ordinary `xs[a:b]` never comes here -- it goes through
                # `_dyn_slice`, which slices without allocating.
                return self._dyn_slice_value(node)
            case ast.Subscript(slice=ast.Slice()):
                return self._dyn_slice(node)
            case ast.Subscript():
                # THE CONTAINER IS COMPUTED FIRST and the index may suspend,
                # which a register does not survive.
                held = self._spill_across_await(self._dyn_expr(node.value),
                                                node.slice)
                index = self._dyn_expr(node.slice)
                out = self.b.call(T.PTR, "apy_getitem", [held(), index])
                self._dyn_check()
                return out
            case ast.Attribute(attr="__name__") if _is_type_call(node.value):
                # `type(x).__name__` stays ONE call. `type(x)` is a real value
                # now, so the general path below would also work -- but this
                # shape is most of what the suite asks of `type`, and going
                # through it avoids interning a type object per call.
                return self.b.call(T.PTR, "apy_type_name",
                                   [self._dyn_expr(node.value.args[0])])
            case ast.Attribute(attr=attr):
                out = self.b.call(T.PTR, "apy_getattr",
                                  [self._dyn_expr(node.value),
                                   self._dyn_attr_literal(attr)])
                self._dyn_check()
                return out
        raise AssertionError(
            f"dynamic lowering reached {type(node).__name__}; "
            f"analysis should have rejected it")

    def _dyn_sequence(self, node, elts: list) -> int:
        """A list or tuple display, built one element at a time.

        The capacity is the element count, so a literal allocates once. The
        elements are evaluated in order before any is stored, which is what
        Python promises and what makes `[f(), g()]` call f first.
        """
        sym = "apy_tuple_new" if isinstance(node, ast.Tuple) else "apy_list_new"
        held = self._held_accumulator(
            self.b.call(T.PTR, sym,
                        [self.b.const(T.I64, max(1, len(elts)))]), elts)
        for element in elts:
            seq = held()
            if isinstance(element, ast.Starred):
                # `[*xs, y]`. The star flattens in place, so the capacity
                # guessed above is a lower bound rather than the answer --
                # which the container grows past on its own.
                self.b.call(T.PTR, "apy_extend",
                            [seq, self._dyn_expr(element.value)])
                self._dyn_check()
                continue
            # THE ELEMENT FIRST: `held()` above is only safe to read after
            # whatever the element does, so the push re-reads it here.
            item = self._dyn_expr(element)
            self.b.call(T.PTR, "apy_seq_push", [held(), item])
        return held()

    def _dyn_fstring(self, parts: list) -> int:
        """An f-string, as a left-to-right chain of concatenations.

        Each interpolation goes through `str()` -- or `repr()` for `!r` --
        which is what an f-string does. A literal piece is a str constant like
        any other, so an f-string with no interpolations is exactly the string
        it looks like.
        """
        out = None
        for at, part in enumerate(parts):
            # WHAT HAS BEEN JOINED SO FAR has to survive an `await` in THIS
            # field or a later one: the chain holds it in a register between
            # pieces, and a register does not cross a suspension. Kept as a
            # READER -- reading it here would put the load before the
            # suspension, which is the bug rather than the fix.
            held = (self._spill_across_await(out, list(parts[at:]))
                    if out is not None else None)
            if isinstance(part, ast.Constant):
                piece = self._dyn_str_literal(part.value)
            else:
                value = self._dyn_expr(part.value)
                # `!r` is repr, `!a` is ascii, anything else and the default
                # are str. The conversion is a character code in the AST, and
                # it runs BEFORE the spec: `f"{x!r:>10}"` pads the repr.
                #
                # `!a` WENT TO `apy_repr` UNTIL THE ESCAPING EXISTED, which
                # made `f"{'café'!a}"` answer `'café'` where CPython says
                # `'caf\\xe9'` -- the two agree for every ASCII value, so
                # nothing noticed until a test wrote a non-ASCII one.
                if part.conversion == ord("a"):
                    value = self.b.call(T.PTR, "apy_ascii", [value])
                    self._dyn_check()
                elif part.conversion == ord("r"):
                    value = self.b.call(T.PTR, "apy_repr", [value])
                    self._dyn_check()
                elif part.conversion == ord("s"):
                    value = self.b.call(T.PTR, "apy_str", [value])
                    self._dyn_check()
                # ALWAYS through `apy_format`, even with no spec: an
                # interpolation is `format(v, "")` and a class defining
                # `__format__` sees the empty spec rather than being sent to
                # `__str__` behind its back.
                # THE SPEC MAY SUSPEND -- it is itself an f-string, and
                # `f"{x:>{await w()}}"` is one that does.
                keep = self._spill_across_await(value, part.format_spec)
                spec = (self._dyn_fstring(part.format_spec.values)
                        if part.format_spec is not None
                        else self._dyn_str_literal(""))
                piece = self.b.call(T.PTR, "apy_format", [keep(), spec])
                self._dyn_check()
            if held is None:
                out = piece
            else:
                out = self.b.call(T.PTR, "apy_add", [held(), piece])
                self._dyn_check()
        if out is None:
            return self._dyn_str_literal("")
        return out

    def _dyn_template(self, parts: list) -> int:
        """A t-string -- PEP 750 -- which does NOT join.

        The literal pieces and the interpolations come out as two tuples, and
        `strings` is one longer than `interpolations` by construction: a piece
        is emitted before each field and one after the last, empty where two
        fields are adjacent or where one begins or ends the template. A
        consumer can then walk them in lockstep, which is the invariant the
        PEP promises and the reason this counts rather than splitting text.

        The values are evaluated HERE, left to right, exactly as an f-string
        evaluates them. What is deferred is the JOINING, not the evaluation --
        a template built from `t"{expensive()}"` has already called it.
        """
        pieces, fields = [], []
        current = ""
        for part in parts:
            if isinstance(part, ast.Constant):
                # ADJACENT CONSTANTS ARE ONE PIECE: an escape can split the
                # literal text in the tree, and `strings` must still have
                # exactly one entry per gap between fields.
                current = current + part.value
                continue
            pieces.append(current)
            current = ""
            fields.append(part)
        pieces.append(current)

        strings = self.b.call(T.PTR, "apy_tuple_new",
                              [self.b.const(T.I64, max(1, len(pieces)))])
        for piece in pieces:
            self.b.call(T.PTR, "apy_seq_push",
                        [strings, self._dyn_str_literal(piece)])
        held_s = self._held_accumulator(strings, fields)
        interps = self._held_accumulator(
            self.b.call(T.PTR, "apy_tuple_new",
                        [self.b.const(T.I64, max(1, len(fields)))]), fields)
        values = self._held_accumulator(
            self.b.call(T.PTR, "apy_tuple_new",
                        [self.b.const(T.I64, max(1, len(fields)))]), fields)
        for part in fields:
            value = self._spill_across_await(self._dyn_expr(part.value), part)
            # THE CONVERSION IS NOT APPLIED, only recorded. `t"{x!r}"` hands
            # the consumer the object and the letter `r`; applying `repr` here
            # would make it text, which is precisely what a template exists
            # not to do.
            conversion = (self._dyn_str_literal(chr(part.conversion))
                          if part.conversion != -1
                          else self.b.call(T.PTR, "apy_none", []))
            # THE SPEC IS EVALUATED, because it may itself interpolate --
            # `t"{x:>{w}}"` records `>8`, not `>{w}`. CPython evaluates it at
            # template construction for the same reason.
            spec = (self._dyn_fstring(part.format_spec.values)
                    if part.format_spec is not None
                    else self._dyn_str_literal(""))
            one = self.b.call(T.PTR, "apy_interpolation_new",
                              [value(), self._dyn_str_literal(part.str),
                               conversion, spec])
            self._dyn_check()
            self.b.call(T.PTR, "apy_seq_push", [interps(), one])
            self.b.call(T.PTR, "apy_seq_push", [values(), value()])
        out = self.b.call(T.PTR, "apy_template_new",
                          [held_s(), interps(), values()])
        self._dyn_check()
        return out

    def _dyn_truth(self, node) -> int:
        """Truthiness of an expression, as the `i1` a branch requires.

        `apy_truth` answers in an i64 because that is the widest thing the ABI
        carries cheaply; `Op.BRANCH` wants an i1 and the verifier says so.
        """
        return self._dyn_truth_of(self._dyn_expr(node))

    def _dyn_truth_of(self, value: int) -> int:
        wide = self.b.call(T.I64, "apy_truth", [value])
        # `cmp`'s `ty` is the OPERAND type; the result is always i1, which is
        # what a branch wants.
        return self.b.cmp(Op.NE, T.I64, wide, self.b.const(T.I64, 0))

    def _dyn_boolop(self, node: ast.BoolOp) -> int:
        """`a and b` yields an OPERAND, not a bool, and short-circuits."""
        out = self.b.reg(T.PTR)
        done = self.b.new_block("boolend")
        last = len(node.values) - 1
        for i, value in enumerate(node.values):
            self.b.emit(Instruction(Op.COPY, T.PTR, dst=out,
                                    args=[self._dyn_expr(value)]))
            if i == last:
                break
            nxt = self.b.new_block("boolnext")
            truth = self._dyn_truth_of(out)
            if isinstance(node.op, ast.And):
                self.b.branch(truth, nxt, done)
            else:
                self.b.branch(truth, done, nxt)
            self.b.switch_to(nxt)
        self.b.jump(done)
        self.b.switch_to(done)
        return out

    def _dyn_compare(self, node: ast.Compare) -> int:
        """`a < b < c` evaluates `b` once and stops at the first false link."""
        out = self.b.reg(T.PTR)
        done = self.b.new_block("cmpend")
        # THE LEFT OPERAND HAS TO OUTLIVE THE RIGHT ONE, which may suspend.
        # A chain moves `left` along, so each link spills across what is still
        # to come rather than only across its own comparator.
        held = self._spill_across_await(self._dyn_expr(node.left),
                                        node.comparators)
        last = len(node.ops) - 1
        for i, (op, right_node) in enumerate(zip(node.ops, node.comparators)):
            right = self._spill_across_await(self._dyn_expr(right_node),
                                             node.comparators[i + 1:])
            left = held()
            right = right()
            negate = isinstance(op, (ast.IsNot, ast.NotIn))
            if isinstance(op, (ast.In, ast.NotIn)):
                # `needle in haystack` -- the operands are the other way round
                # from every other comparison, which is worth saying out loud
                # because reading it as a comparison silently reverses it.
                result = self.b.call(T.PTR, "apy_contains", [left, right])
            else:
                sym = DYN_CMP[ast.Is if negate else type(op)]
                result = self.b.call(T.PTR, sym, [left, right])
            self._dyn_check()
            if negate:
                result = self._dyn_not(result)
            self.b.emit(Instruction(Op.COPY, T.PTR, dst=out, args=[result]))
            if i == last:
                break
            nxt = self.b.new_block("cmpnext")
            self.b.branch(self._dyn_truth_of(out), nxt, done)
            self.b.switch_to(nxt)
            # THE NEXT LINK'S LEFT OPERAND is this link's right one, and it
            # has to outlive whatever comes after it in the chain.
            held = self._spill_across_await(right, node.comparators[i + 1:])
        self.b.jump(done)
        self.b.switch_to(done)
        return out

    def _dyn_ifexp(self, node: ast.IfExp) -> int:
        out = self.b.reg(T.PTR)
        then_b = self.b.new_block("ifexpthen")
        else_b = self.b.new_block("ifexpelse")
        done = self.b.new_block("ifexpend")
        self.b.branch(self._dyn_truth(node.test), then_b, else_b)
        self.b.switch_to(then_b)
        self.b.emit(Instruction(Op.COPY, T.PTR, dst=out,
                                args=[self._dyn_expr(node.body)]))
        self.b.jump(done)
        self.b.switch_to(else_b)
        self.b.emit(Instruction(Op.COPY, T.PTR, dst=out,
                                args=[self._dyn_expr(node.orelse)]))
        self.b.jump(done)
        self.b.switch_to(done)
        return out

    # ── calls, and the boundary ─────────────────────────────────────────────
    def _dyn_init_module_defs(self) -> None:
        """Pre-bind every module-level `def`, at program start.

        WITHOUT ITS DEFAULTS OR ITS DECORATORS. The `def` statement is still
        walked where it is written, and that is where both belong: `n = 1` then
        `def f(v=n)` then `n = 99` must capture 1, and evaluating the default
        at program start reached `n` before the module had bound it -- a
        NameError for a program CPython runs. The same went for a decorator
        naming something the module body assigns.

        The bare pre-binding stays, so `f` can be passed, stored and compared
        before its `def` is reached. That is a superset of CPython, which
        answers NameError there; the statement rebinds the real value.
        """
        for key, info in self.infos.items():
            if key == ENTRY_NAME or not info.dynamic or "." in key:
                continue
            func = self._dyn_function_value(key, key, with_defaults=False)
            if key in self.module_names:
                self._dyn_global_write(key, func)

    def _dyn_default(self, info, index: int) -> int:
        addr = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr,
                                sym=self.default_symbol(info, index)))
        return self.b.load(T.PTR, addr)

    def _dyn_unbound_method(self, base: str, attr: str) -> int:
        """`str.lower` and friends, as a one-argument callable value.

        The same synthesised thunk a builtin gets, with a method call for a
        body. The receiver is the thunk's argument, which is exactly the
        unbound-method rule -- `str.lower(x)` is `x.lower()` -- so nothing has
        to know that a type object was involved. There are no type objects
        here yet, which is why this is a shape rather than an attribute
        lookup.
        """
        key = f"{base}.{attr}"
        symbol = self._builtin_thunks.get(key)
        if symbol is None:
            symbol = f"pybm_{base}_{attr}"
            self._builtin_thunks[key] = symbol
            self._pending_thunks.append((key, symbol))
        code = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.FUNC_ADDR, T.PTR, dst=code, sym=symbol))
        return self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, 1),
                            self._dyn_str_literal(key),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0)])

    def _dyn_annotate_thunk(self, key: str, info) -> int | None:
        """PEP 649: the zero-argument function that BUILDS `__annotations__`.

        Lazy rather than a dict evaluated at the `def`, because an annotation
        may name something that does not exist yet -- `def f(x: Undefined)` is
        a legal definition and only READING its annotations is an error. The
        thunk is emitted alongside the builtin ones and its body is the dict
        display the annotations spell out.
        """
        node = info.node
        pairs = []
        for arg in (list(getattr(node.args, "posonlyargs", []))
                    + list(node.args.args) + list(node.args.kwonlyargs)):
            if arg.annotation is not None:
                pairs.append((arg.arg, arg.annotation))
        if node.returns is not None:
            pairs.append(("return", node.returns))
        return self._dyn_annotate_of(key, pairs)

    def _dyn_annotate_of(self, key: str, pairs: list) -> int | None:
        """The thunk for a given set of (name, annotation) pairs.

        Shared by `def` and by `class`: a class body's annotated names build
        `C.__annotations__` the same lazy way a function's parameters do.
        """
        if not pairs:
            return None
        symbol = "pyann_" + "".join(c if c.isalnum() or c == "_" else "_"
                                    for c in key)
        # ONE BODY PER `def`, however many times its value is built. A
        # module-level `def` is pre-bound at program start and rebuilt at its
        # statement, and emitting the thunk both times is two definitions of
        # one C function.
        if not any(sym == symbol for sym, _ in self._pending_annotations):
            self._pending_annotations.append((symbol, pairs))
        code = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.FUNC_ADDR, T.PTR, dst=code, sym=symbol))
        return self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, 0),
                            self._dyn_str_literal("__annotate__"),
                            self.b.const(T.I64, 0), self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0)])

    def _dyn_emit_annotate_thunks(self) -> None:
        """Emit each annotation thunk's body: a dict of name to annotation."""
        i = 0
        while i < len(self._pending_annotations):
            symbol, pairs = self._pending_annotations[i]
            i += 1
            fn = Function(symbol, T.PTR, linkage=Linkage.INTERNAL)
            fn.params.append(fn.new_register(T.PTR))    # the function value
            saved_b, saved_info = self.b, self.info
            self.b = Builder(fn)
            self.b.switch_to(self.b.new_block("entry"))
            out = self.b.call(T.PTR, "apy_dict_new",
                              [self.b.const(T.I64, len(pairs) + 1)])
            for pname, expr in pairs:
                if isinstance(expr, ast.Name) and not self._name_exists(
                        expr.id):
                    # AN ANNOTATION MAY NAME SOMETHING THAT DOES NOT EXIST.
                    # `def f(x: Undefined)` is a legal definition -- PEP 649
                    # made the annotations lazy precisely so that only READING
                    # them is the error, and the read is here.
                    self.b.call(T.PTR, "apy_raise",
                                [self.b.call(
                                    T.PTR, "apy_make_exc",
                                    [self._dyn_str_literal("NameError"),
                                     self._dyn_str_literal(
                                         f"name '{expr.id}' is not "
                                         f"defined")])])
                    # NULL, which is how every runtime entry point reports a
                    # failure to its caller. `_dyn_check_forced` would decide
                    # this thunk has nothing enclosing it and make the error
                    # fatal, where the program is entitled to catch it.
                    self.b.ret(self.b.const(T.PTR, 0))
                    break
                self.b.call(T.PTR, "apy_dict_set",
                            [out, self._dyn_str_literal(pname),
                             self._dyn_expr(expr)])
                self._dyn_check()
            # The block may already be terminated by the NameError path
            # above, which returns null rather than a dict.
            if self.b.current.terminator is None:
                self.b.ret(out)
            self.module.functions.append(fn)
            self.b, self.info = saved_b, saved_info

    def _name_exists(self, name: str) -> bool:
        """Whether a bare name resolves to anything at module scope.

        Used only by the annotation thunks, which are the one place a name may
        legitimately not resolve -- see `_dyn_emit_annotate_thunks`.
        """
        return (name in self.module_names or name in self.class_names
                or name in _VALUE_BUILTINS or name in _BUILTIN_TYPE_VALUES
                or name in _CLASS_BUILTINS
                or name in _EXC_NAMES or name in self.exc_classes
                or name in MODULE_DUNDERS or name in self.infos)

    def _dyn_scope_dict(self, name: str) -> int:
        """The mapping `locals()` or `globals()` answers.

        Shared with `dir()`, which with no argument is the names in
        scope -- `sorted(locals())` and nothing else.
        """
            # A SNAPSHOT, built name by name at the call site -- which is the
        # only place the mapping from a name to the register or global
        # holding it still exists. PEP 667 wants exactly a snapshot: a
        # write to the dict must not reach the local, and a later
        # assignment to the local must not show up in the dict. Both fall
        # out of having built an ordinary dict.
        out = self.b.call(T.PTR, "apy_dict_new", [self.b.const(T.I64, 1)])
        if name == "globals" or self.info.name == ENTRY_NAME:
            # `globals()` anywhere, and `locals()` at module level, where
            # the two ARE the same mapping.
            # THE MODULE DUNDERS ARE IN IT TOO. `globals()` is the
            # module's namespace, and `"__builtins__" in globals()` is a
            # question programs actually ask.
            for key in sorted(MODULE_DUNDERS):
                out = self.b.call(
                    T.PTR, "apy_locals_put",
                    [out, self._dyn_str_literal(key),
                     self._dyn_load(key)])
            out = self.b.call(
                T.PTR, "apy_locals_put",
                [out, self._dyn_str_literal("__builtins__"),
                 self._dyn_load("__builtins__")])
            for key in self.module_names:
                addr = self.b.reg(T.PTR)
                self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr,
                                        sym=self.global_symbol(key)))
                # The RAW load, not `_dyn_global_read`: a global that has
                # not been assigned yet is absent from the mapping, not a
                # NameError.
                out = self.b.call(
                    T.PTR, "apy_locals_put",
                    [out, self._dyn_str_literal(key),
                     self.b.load(T.PTR, addr)])
        else:
            for key, sym in self.info.locals.items():
                value = (self.b.call(T.PTR, "apy_cell_get", [sym.register])
                         if sym.storage in (CELL, FREE) else sym.register)
                out = self.b.call(T.PTR, "apy_locals_put",
                                  [out, self._dyn_str_literal(key), value])
        return out

    def _dyn_register_builtin_types(self) -> None:
        """Build the canonical thunk for every builtin type the module names.

        Run at the top of the entry, before any user statement, because
        `apy_type_of` answers with whatever has been registered for that kind
        and `apy_func_is_type` is what registers it. Doing it lazily made
        `type(1) is int` depend on which side was evaluated first.

        Only the names the module actually mentions -- a program that never
        writes `int` needs no thunk for it, and `type(1)` still answers a type
        object with the right name and repr.
        """
        entry = self.infos.get(ENTRY_NAME)
        if entry is None:
            return
        # Every function's body, not only the module's: `int` named inside a
        # `def` registers the same canonical thunk, and a `def` is lifted out
        # of the entry's own AST.
        mentioned = set()
        for info in self.infos.values():
            for node in ast.walk(info.node):
                if isinstance(node, ast.Name):
                    mentioned.add(node.id)
        for key in sorted(_BUILTIN_TYPE_VALUES & mentioned):
            self._dyn_builtin_value(key)

    def _dyn_builtin_value(self, name: str) -> int:
        """A builtin as a callable value, via a synthesised one-argument thunk.

        The thunk is an ordinary IR function whose body is the call the
        frontend would have emitted for `name(x)` written out -- so the
        builtin has exactly one implementation and a value-form that cannot
        drift from it. One thunk per builtin per module, emitted on first use.

        This is why `_VALUE_BUILTINS` is one-argument only: the thunk's arity
        is baked into its shape, and a variadic builtin has no single one.
        """
        symbol = self._builtin_thunks.get(name)
        if symbol is None:
            symbol = f"pyb_{name}"
            self._builtin_thunks[name] = symbol
            self._pending_thunks.append((name, symbol))
        code = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.FUNC_ADDR, T.PTR, dst=code, sym=symbol))
        variadic = 1 if name in _VARIADIC_THUNKS else 0
        # ONE OPTIONAL PARAMETER for the types whose zero-argument form is
        # legal, so `defaultdict(list)` can call the value with nothing.
        optional = 1 if (name in _EMPTY_DEFAULTS
                         and name not in _VARIADIC_THUNKS) else 0
        made = self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, 1),
                            self._dyn_str_literal(name),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, optional),
                            self.b.const(T.I64, variadic)])
        if optional:
            self.b.call(T.PTR, "apy_func_default",
                        [made, self.b.const(T.I64, 0),
                         self._dyn_empty_value(name)])
        if name not in _BUILTIN_TYPE_VALUES:
            # A BUILTIN REACHED AS A VALUE. `type(print).__name__` is
            # `builtin_function_or_method` in CPython, and a synthesised thunk
            # is an ordinary compiled function here without the flag.
            self.b.call(T.PTR, "apy_func_builtin", [made])
        if name in _BUILTIN_TYPE_VALUES:
            # STILL CALLABLE, but it is a TYPE: `print(int)` says
            # `<class 'int'>` and `isinstance(x, t)` works through it.
            # THE RETURNED VALUE, not the one passed in: the runtime hands
            # back the canonical thunk for this name, so two mentions of
            # `int` are one object and `int == int` is True.
            made = self.b.call(T.PTR, "apy_func_is_type", [made])
        return made

    def _dyn_empty_value(self, name: str) -> int:
        """What `list()` and friends answer with no argument.

        Handed to the thunk as its parameter's DEFAULT, so the body converts
        an already-empty value of the right type rather than branching on
        whether it was called with anything.
        """
        want = _EMPTY_DEFAULTS[name]
        if want == "":
            return (self._dyn_bytes_literal(b"") if name == "bytes"
                    else self._dyn_str_literal(""))
        if want is False:
            return self.b.call(T.PTR, "apy_from_bool",
                               [self.b.const(T.I64, 0)])
        if want == 0:
            return self.b.call(T.PTR, "apy_from_int", [self.b.const(T.I64, 0)])
        if want == 0.0:
            return self.b.call(T.PTR, "apy_from_float",
                               [self.b.const(T.F64, 0.0)])
        return self.b.call(T.PTR, want, [self.b.const(T.I64, 1)])

    def _dyn_emit_thunks(self) -> None:
        """Emit the body of every builtin thunk this module asked for.

        Run after the real functions, because using a builtin as a value is
        what creates the need for one and that is discovered while lowering
        them. The list can grow while it is being drained -- a thunk's own
        body is lowered here and could in principle mention another builtin --
        so it is walked by index rather than iterated.
        """
        import ast as _ast
        i = 0
        while i < len(self._pending_thunks):
            name, symbol = self._pending_thunks[i]
            i += 1
            fn = Function(symbol, T.PTR, linkage=Linkage.INTERNAL)
            # TWO parameters. `apy_invoke` passes the function VALUE first to
            # every callable -- that slot is where a closure finds its cells --
            # and a thunk that declared only one read the function object as
            # its argument, so `repr` as a value returned the repr of itself.
            env = fn.new_register(T.PTR)
            fn.params.append(env)
            arg = fn.new_register(T.PTR)
            fn.params.append(arg)
            saved_b, saved_info = self.b, self.info
            saved_handlers, self.handlers = self.handlers, []
            saved_finallys, self.finallys = self.finallys, []
            # ITS OWN `info`, so a failure inside it RETURNS rather than
            # stopping the process -- see `_Synthetic`.
            self.info = _Synthetic(symbol)
            self.b = Builder(fn)
            self.b.switch_to(self.b.new_block("entry"))
            # A synthetic call whose single argument is already a value. The
            # `_Given` node hands the register straight back, so the ordinary
            # builtin lowering runs unchanged rather than being reimplemented.
            if name in _VARIADIC_THUNKS:
                # The single parameter IS the `*rest` tuple the caller built,
                # so the body is one call that takes it whole.
                self.b.ret(self.b.call(T.PTR, _VARIADIC_THUNKS[name], [arg]))
                self.module.functions.append(fn)
                self.b, self.info = saved_b, saved_info
                self.handlers, self.finallys = saved_handlers, saved_finallys
                continue
            if "." in name:
                # An unbound method: the thunk's argument is the RECEIVER.
                _, attr = name.split(".", 1)
                call = _ast.Call(
                    func=_ast.Attribute(value=_Given(arg), attr=attr,
                                        ctx=_ast.Load()),
                    args=[], keywords=[])
            else:
                call = _ast.Call(func=_ast.Name(id=name, ctx=_ast.Load()),
                                 args=[_Given(arg)], keywords=[])
            for node in _ast.walk(call):
                node.lineno = node.end_lineno = 1
                node.col_offset = node.end_col_offset = 0
            # THE BUILTIN, not whatever the module bound to that name. A
            # program that writes `len = 5` makes `len` a module name, and the
            # thunk's body would then read the global -- which falls back to
            # this very thunk when the global is empty. That is an infinite
            # recursion, and it is the thunk's whole job to be the way out.
            saved_raw, self._raw_builtin = self._raw_builtin, name
            out = self._dyn_call(call)
            if self.b.current.terminator is None:
                self.b.ret(out)
            self._raw_builtin = saved_raw
            self.module.functions.append(fn)
            self.b, self.info = saved_b, saved_info
            self.handlers, self.finallys = saved_handlers, saved_finallys

    def _dyn_star_args(self, node: ast.Call) -> int:
        """The argument list of a call containing `*xs`, built at run time.

        Each ordinary argument is appended, and each starred one is flattened
        in place, so `f(1, *xs, 2)` produces exactly the sequence CPython
        passes. The result is a list because the COUNT is not known here --
        that is what a star means -- and a list is the only shape that can
        carry a count decided at run time.
        """
        held = self._held_accumulator(
            self.b.call(T.PTR, "apy_list_new",
                        [self.b.const(T.I64, max(1, len(node.args)))]),
            node.args)
        for arg in node.args:
            # THE ARGUMENT FIRST, the list after: one that suspends leaves a
            # register the resume never wrote -- the same shape every other
            # display here takes.
            if isinstance(arg, ast.Starred):
                more = self._dyn_expr(arg.value)
                self.b.call(T.PTR, "apy_extend", [held(), more])
                self._dyn_check()
            else:
                one = self._dyn_expr(arg)
                self.b.call(T.PTR, "apy_seq_push", [held(), one])
        return held()

    def _dyn_spread_call(self, node: ast.Call) -> int:
        """`f(*xs)`, including `obj.m(*xs)`.

        The callee is evaluated as a VALUE rather than called directly: a
        direct call carries a fixed IR arity, and there is no number to give
        it. `apy_call_spread` then binds the list against the callee's own
        signature, so defaults, `*rest` and a wrong count all behave exactly
        as they do for an ordinary call -- reported at run time, which is
        where CPython reports them for this shape too.
        """
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"):
            # `"{} {}".format(*xs)`. `str.format` IS NOT A VALUE here -- the
            # method is chosen by NAME at the call site, exactly as
            # `_dyn_method` does it -- so reaching it through `apy_getattr`
            # answered "'str' object has no attribute 'format'" for a call
            # every program writes. The positional arguments arrive as the
            # run-time list the spread built, which `apy_str_format` walks the
            # same way it walks a tuple.
            held_recv = self._spill_across_await(
                self._dyn_expr(node.func.value),
                list(node.args) + [kw.value for kw in node.keywords])
            packed = self._spill_across_await(
                self._dyn_star_args(node),
                [kw.value for kw in node.keywords])
            named = self.b.call(T.PTR, "apy_dict_new",
                                [self.b.const(T.I64,
                                              len(node.keywords) + 1)])
            for kw in node.keywords:
                if kw.arg is None:
                    self.b.call(T.PTR, "apy_update",
                                [named, self._dyn_expr(kw.value)])
                else:
                    self.b.call(T.PTR, "apy_dict_set",
                                [named, self._dyn_str_literal(kw.arg),
                                 self._dyn_expr(kw.value)])
                self._dyn_check()
            out = self.b.call(T.PTR, "apy_str_format",
                              [held_recv(), packed(), named])
            self._dyn_check()
            return out
        if isinstance(node.func, ast.Attribute):
            callee = self.b.call(T.PTR, "apy_getattr",
                                 [self._dyn_expr(node.func.value),
                                  self._dyn_attr_literal(node.func.attr)])
            self._dyn_check()
        else:
            callee = self._dyn_expr(node.func)
        # THE CALLEE IS EVALUATED FIRST and everything after it may suspend,
        # which a register does not survive.
        later = list(node.args) + [kw.value for kw in node.keywords]
        held_callee = self._spill_across_await(callee, later)
        # THE KEYWORDS TRAVEL SEPARATELY, as they do for every other call
        # shape: appended to the argument list they would arrive as one more
        # positional. Dropping them made `f(*xs, **kw)` ignore every keyword.
        named = [kw.value for kw in node.keywords]
        held_spread = self._spill_across_await(self._dyn_star_args(node),
                                               named)
        values = []
        for i, kw in enumerate(node.keywords):
            values.append(self._spill_across_await(self._dyn_expr(kw.value),
                                                   named[i + 1:]))
        kwd = self.b.call(T.PTR, "apy_dict_new",
                          [self.b.const(T.I64, len(node.keywords) + 1)])
        for kw, reader in zip(node.keywords, values):
            if kw.arg is None:
                self.b.call(T.PTR, "apy_update", [kwd, reader()])
            else:
                self.b.call(T.PTR, "apy_dict_set",
                            [kwd, self._dyn_str_literal(kw.arg), reader()])
            self._dyn_check()
        out = self.b.call(T.PTR, "apy_call_spread_kw",
                          [held_callee(), held_spread(), kwd])
        self._dyn_check()
        return out

    def _dyn_arguments(self, node: ast.Call, info) -> list:
        """One value per parameter: positional, then keyword, then default.

        Resolved HERE rather than in the callee, because the signature is known
        at compile time -- so a keyword argument costs nothing at run time and
        the callee needs no notion of one.
        """
        params = [p.name for p in info.params]
        # A KEYWORD-ONLY PARAMETER TAKES NO ARGUMENT POSITION. It occupies a
        # slot -- the callee reads them all by index -- but a positional
        # argument can never reach it, and one that ran past the positional
        # parameters belongs to `*args`. Filling by index alone gave
        # `def b(x, *args, c=3)` called `b(1, 2)` the answer `(1, (), 2)`: the
        # `2` landed in `c` and `*args` came back empty.
        upto = len(params) - info.kwonly
        # EVERY VALUE HAS TO SURVIVE THE ONES AFTER IT. `f(await a(), await
        # b())` computes the first into a register and suspends inside the
        # second -- and a register does not cross a suspension, so the call
        # read one no path had written. `slots` therefore holds READERS while
        # the arguments are being evaluated, and is resolved at the end.
        slots: list = [None] * len(params)
        given = list(node.args[:upto])
        after = list(node.args[upto:]) + [kw.value for kw in node.keywords]
        for i, arg in enumerate(given):
            slots[i] = self._spill_across_await(self._dyn_expr(arg),
                                                given[i + 1:] + after)
        by_name = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        named = [kw.value for kw in node.keywords]
        for i, pname in enumerate(params):
            if i < info.posonly and pname in by_name:
                # A POSITIONAL-ONLY PARAMETER CANNOT BE NAMED. The runtime
                # binder already refuses this for a call through a value; a
                # direct call resolves names here and filled the slot anyway,
                # so `f(a=1)` against `def f(a, /)` quietly worked. Raised
                # rather than refused at compile time, because CPython raises
                # and a program may catch it.
                self.b.call(T.PTR, "apy_raise",
                            [self.b.call(
                                T.PTR, "apy_make_exc",
                                [self._dyn_str_literal("TypeError"),
                                 self._dyn_str_literal(
                                     f"{info.name}() got some "
                                     f"positional-only arguments passed as "
                                     f"keyword arguments: '{pname}'")])])
                self._dyn_check()
            if slots[i] is None and pname in by_name:
                at = named.index(by_name[pname])
                slots[i] = self._spill_across_await(
                    self._dyn_expr(by_name[pname]), named[at + 1:])
        first_default = len(params) - len(info.defaults)
        for i, value in enumerate(slots):
            if value is None:
                slots[i] = self._holder(
                    self._dyn_default(info, i - first_default))
        if info.vararg is not None:
            extra = list(node.args[upto:])
            held = [self._spill_across_await(self._dyn_expr(arg),
                                             extra[i + 1:])
                    for i, arg in enumerate(extra)]
            rest = self.b.call(T.PTR, "apy_tuple_new",
                               [self.b.const(T.I64, max(1, len(extra)))])
            for reader in held:
                self.b.call(T.PTR, "apy_seq_push", [rest, reader()])
            slots.append(self._holder(rest))
        # READ ONLY NOW, when nothing else can suspend before the call.
        return [one() for one in slots]

    def _dyn_argv(self, values: list) -> int:
        """A stack array of values, and its address.

        The IR has no varargs, so every runtime entry point taking an unknown
        number of arguments takes a pointer and a count instead -- `apy_print`
        was the first and the shape is now shared by every one of them.
        """
        buf = self.b.alloca(max(1, len(values)) * 8)
        for i, value in enumerate(values):
            slot = buf if i == 0 else self.b.offset(
                buf, self.b.const(T.I64, i * 8))
            self.b.store(T.PTR, value, slot)
        return buf

    def _dyn_multi_builtin(self, name: str, node: ast.Call):
        """The builtins whose SHAPE depends on how they were called.

        `round(x)` returns an int and `round(x, n)` a float; `min(xs)` scans an
        iterable and `min(a, b)` compares two values. These are different
        functions wearing one name, so the argument count picks between them
        here rather than a single runtime entry point guessing from kinds --
        `min([3], [1])` and `min([3, 1])` would be indistinguishable to one
        that tried.

        Returns None when this call is the ordinary shape, so the caller falls
        through to the one-argument table.
        """
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        n = len(node.args)

        def arg(i):
            return self._dyn_expr(node.args[i])

        def done(reg):
            self._dyn_check()
            return reg

        if name == "slice" and n in (1, 2, 3):
            # `slice(stop)` puts the single argument in STOP, not start --
            # the same shape as `range(stop)`, and the reason this cannot be
            # a plain positional forward.
            none = lambda: self.b.call(T.PTR, "apy_none", [])
            if n == 1:
                parts = [none(), arg(0), none()]
            elif n == 2:
                parts = [arg(0), arg(1), none()]
            else:
                parts = [arg(0), arg(1), arg(2)]
            return done(self.b.call(T.PTR, "apy_slice_new", parts))
        if name in _DESCRIPTOR_KINDS and n == 1:
            return done(self.b.call(
                T.PTR, "apy_descr_new",
                [arg(0), self.b.const(T.I64, _DESCRIPTOR_KINDS[name])]))
        if name == "getattr" and n == 3:
            # `getattr(x, 'a', fallback)` -- the THREE-argument form, which is
            # a different function from the two-argument one: it answers the
            # fallback where the other raises. Without this the frontend
            # emitted a three-argument call to the two-argument runtime
            # entry point, and the C backend refused to compile the program.
            return done(self.b.call(T.PTR, "apy_getattr_default",
                                    [arg(0), arg(1), arg(2)]))
        if name == "round" and n == 2:
            return done(self.b.call(T.PTR, "apy_round_to", [arg(0), arg(1)]))
        if name == "int" and n == 2:
            return done(self.b.call(T.PTR, "apy_to_int_base",
                                    [arg(0), arg(1)]))
        if name == "int" and n == 0:
            # `int()` IS 0 -- the type's zero value, not a conversion of
            # nothing, and the conversion path reads an argument that is not
            # there.
            return done(self.b.call(T.PTR, "apy_from_int",
                                    [self.b.const(T.I64, 0)]))
        if name == "sum" and n == 2:
            return done(self.b.call(T.PTR, "apy_sum_from", [arg(0), arg(1)]))
        if name in ("min", "max"):
            want = self.b.const(T.I64, 1 if name == "max" else 0)
            if n >= 2:
                values = [arg(i) for i in range(n)]
                return done(self.b.call(T.PTR, "apy_extreme_n",
                                        [self._dyn_argv(values),
                                         self.b.const(T.I64, n), want]))
            if "default" in kw:
                keyfn = (self._dyn_expr(kw["key"]) if "key" in kw
                         else self.b.call(T.PTR, "apy_none", []))
                return done(self.b.call(T.PTR, "apy_extreme_or",
                                        [arg(0), keyfn,
                                         self._dyn_expr(kw["default"]), want]))
            return None
        if name == "zip" and n != 2:
            values = [arg(i) for i in range(n)]
            strict = (self.b.call(T.I64, "apy_truth",
                                  [self._dyn_expr(kw["strict"])])
                      if "strict" in kw else self.b.const(T.I64, 0))
            return done(self.b.call(T.PTR, "apy_zip_n",
                                    [self._dyn_argv(values),
                                     self.b.const(T.I64, n), strict]))
        if name == "zip" and "strict" in kw:
            values = [arg(0), arg(1)]
            return done(self.b.call(
                T.PTR, "apy_zip_n",
                [self._dyn_argv(values), self.b.const(T.I64, 2),
                 self.b.call(T.I64, "apy_truth",
                             [self._dyn_expr(kw["strict"])])]))
        if name == "iter" and n == 2:
            return done(self.b.call(T.PTR, "apy_iter_until",
                                    [arg(0), arg(1)]))
        if name == "format":
            # `format(v)` is `str(v)`, and `format(v, spec)` is the whole
            # mini-language -- one entry point, because an empty spec IS the
            # first form.
            return done(self.b.call(
                T.PTR, "apy_format",
                [arg(0), (arg(1) if n > 1 else self._dyn_str_literal(""))]))
        if name == "pow":
            # `pow(a, b)` IS `a ** b`, and the three-argument form is modular
            # exponentiation -- a different operation with no operator, which
            # is why it exists as a builtin at all.
            if n == 3:
                return done(self.b.call(T.PTR, "apy_pow3",
                                        [arg(0), arg(1), arg(2)]))
            if n == 2:
                return done(self.b.call(T.PTR, "apy_pow", [arg(0), arg(1)]))
        return None

    def _dyn_indirect(self, callee: int, args: list, keywords=()) -> int:
        """Call a VALUE: a class, a closure, a bound method, a parameter.

        The arguments go in a stack slot and their address is passed, the same
        shape `print` uses and for the same reason -- the IR has no varargs.
        `apy_call` is one entry point for every callable kind, so a call site
        never has to know whether it is holding a class or a function; `C(...)`
        allocating an instance and running `__init__` happens inside it.

        KEYWORD arguments travel as a DICT built here and placed by
        `apy_call_kw`. They cannot be resolved at the call site: the callee is
        a value, so which parameter `swallow=` names is known only to the
        function object it turns out to hold. A dict rather than a list of
        name/value pairs because `**d` merges one whose keys are not known
        until it exists, and one shape for both is one implementation.
        """
        if not keywords:
            buf = self._dyn_argv(args)
            out = self.b.call(T.PTR, "apy_call",
                              [callee, buf, self.b.const(T.I64, len(args))])
            self._dyn_check()
            return out
        # THE KEYWORD VALUES BEFORE THE ARGUMENT BUFFER. The buffer is a STACK
        # slot, and a suspension inside a keyword value returns through the
        # step function -- so whatever was stored in it beforehand is gone by
        # the time the call reads it, and `f(1, k=await g())` handed the
        # callee garbage. Evaluating them here is also Python's order: the
        # positional arguments, which the caller has already evaluated, then
        # these, left to right.
        later = [kw.value for kw in keywords]
        held = [self._spill_across_await(one, later) for one in args]
        callee_r = self._spill_across_await(callee, later)
        values = []
        for i, kw in enumerate(keywords):
            values.append(self._spill_across_await(self._dyn_expr(kw.value),
                                                   later[i + 1:]))
        kwd = self.b.call(T.PTR, "apy_dict_new",
                          [self.b.const(T.I64, len(keywords) + 1)])
        for kw, reader in zip(keywords, values):
            if kw.arg is None:
                # `**d`, in SOURCE ORDER with the explicit keywords around it,
                # so a later one wins -- `f(**d, k=1)` and `f(k=1, **d)` are
                # different calls and CPython keeps the difference.
                self.b.call(T.PTR, "apy_update", [kwd, reader()])
            else:
                self.b.call(T.PTR, "apy_dict_set",
                            [kwd, self._dyn_str_literal(kw.arg), reader()])
            self._dyn_check()
        buf = self._dyn_argv([r() for r in held])
        out = self.b.call(T.PTR, "apy_call_kw",
                          [callee_r(), buf, self.b.const(T.I64, len(args)),
                           kwd])
        self._dyn_check()
        return out

    def _dyn_type_params(self, node) -> list:
        """PEP 695's type parameters, bound before the definition they belong
        to. Answers the objects, so they can be read back off it after."""
        made = []
        for one in getattr(node, "type_params", ()) or ():
            held = self.b.call(T.PTR, "apy_typevar",
                               [self._dyn_str_literal(one.name)])
            # PEP 696: `class Box[T = int]`. The default is an expression
            # evaluated where the definition runs, exactly as a parameter's
            # default is -- so it is set on the object rather than baked in.
            if getattr(one, "default_value", None) is not None:
                self.b.call(T.PTR, "apy_typevar_default",
                            [held, self._dyn_expr(one.default_value)])
            self._dyn_store(one.name, held)
            made.append(held)
        return made

    def _dyn_record_type_params(self, node, made: list) -> None:
        """`first.__type_params__` -- where a program looks for them."""
        if not made:
            return
        tup = self.b.call(T.PTR, "apy_tuple_new",
                          [self.b.const(T.I64, len(made) + 1)])
        for one in made:
            self.b.call(T.PTR, "apy_seq_push", [tup, one])
        self.b.call(T.PTR, "apy_setattr",
                    [self._dyn_load(node.name),
                     self._dyn_str_literal("__type_params__"), tup])
        self._dyn_check()

    def _dyn_super(self) -> int:
        """What `super()` evaluates to, inside a method.

        The DEFINING class is baked in here, not read from `type(self)` at run
        time, and that is the whole correctness argument: with `B(A)` and
        `C(B)`, a `super().m()` written in B's `m` must find A's. Starting from
        `type(self)` would find B's own `m` for a C instance and recurse until
        the stack ran out. Only the frontend knows which class the method was
        written in, so only the frontend can supply it.
        """
        owner = self.classes[self.info.owner]
        out = self.b.call(T.PTR, "apy_super",
                          [self._dyn_load(owner.name),
                           self._dyn_load(self.info.params[0].name)])
        self._dyn_check()
        return out

    def _dyn_super_explicit(self, node: ast.Call) -> int:
        """`super(C, self)` -- the same object, with both halves written out.

        The pair IS what the no-argument form synthesises, so there is one
        runtime call and the only difference is where the class and the
        receiver come from.
        """
        out = self.b.call(T.PTR, "apy_super",
                          [self._dyn_expr(node.args[0]),
                           self._dyn_expr(node.args[1])])
        self._dyn_check()
        return out

    #: How a boxed value becomes the machine value a native signature wants,
    #: and how the answer comes back. Keyed by the IR type `cffi.TYPES` gives.
    _CFFI_FLOATS = ("f32", "f64")

    def _cffi_width(self, value: int, src: T.Type, dst: T.Type) -> int:
        """One value at the width the native signature declares.

        The same opcode choice `lower._coerce` makes, and it has to be: the
        declared width is what the callee reads, so handing it a 64-bit value
        where it wants 32 is the truncation `cffi.py` refuses to guess about.
        """
        if src == dst:
            return value
        out = self.b.reg(dst)
        if src.is_int and dst.is_int and src.bits == dst.bits:
            op = Op.BITCAST
        elif src.is_float and dst.is_float:
            op = Op.FTOF
        elif src.is_float:
            op = Op.FTOI
        elif dst.is_float:
            op = Op.ITOF
        elif dst.bits > src.bits:
            op = Op.EXTEND
        else:
            op = Op.TRUNC
        self.b.emit(Instruction(op, dst, dst=out, args=[value]))
        return out

    def _dyn_ctypes_call(self, node: ast.Call, native: dict) -> int:
        """`lib.sqrt(x)` from code with no static types.

        THE SAME ONE `Op.CALL` the static path emits, with a conversion either
        side of it. A dynamic value is an `apy_value` and a native signature
        wants a machine word, so each argument is unboxed to the width its
        `argtypes` declares and the result is boxed back.

        WHY THIS EXISTS AT ALL: every BUNDLED module is dynamic Python, so
        without it the standard library cannot reach a C library, and
        `libm.sqrt(x)` inside an untyped function raised `NameError: name
        'libm' is not defined` -- about a library the source declares three
        lines above. See docs/STDLIB.md.

        A POINTER ARGUMENT IS A STRING'S BYTES. The kind is checked at run
        time by `apy_str_bytes`, because a dynamic value's kind is a run-time
        fact -- and handing a native function the address of an integer cell
        is how a ctypes program corrupts memory rather than failing.
        """
        args = []
        for arg, want in zip(node.args, native["params"]):
            value = self._dyn_expr(arg)
            if want == "ptr":
                args.append(self.b.call(T.PTR, "apy_str_bytes", [value]))
                self._dyn_check()
                continue
            floaty = want in self._CFFI_FLOATS
            raw = self.b.call(T.F64 if floaty else T.I64,
                              "apy_as_float" if floaty else "apy_as_int",
                              [value])
            self._dyn_check()
            args.append(self._cffi_width(raw, T.F64 if floaty else T.I64,
                                         TO_IR[sem_type(want)]))
        ret = native["ret"]
        if ret == "void":
            self.b.call(None, native["symbol"], args)
            return self.b.call(T.PTR, "apy_none", [])
        result = self.b.call(TO_IR[sem_type(ret)], native["symbol"], args)
        if ret == "ptr":
            # AN ADDRESS COMES BACK AS AN INTEGER, which is what CPython does
            # for a `c_void_p` restype. It answers None for NULL and this
            # answers 0; a program testing the result for truth reads both the
            # same way, and that is the whole of what a handle is used for.
            return self.b.call(T.PTR, "apy_from_int", [result])
        floaty = ret in self._CFFI_FLOATS
        wide = self._cffi_width(result, TO_IR[sem_type(ret)],
                                T.F64 if floaty else T.I64)
        return self.b.call(T.PTR,
                           "apy_from_float" if floaty else "apy_from_int",
                           [wide])

    def _dyn_hostsvc_call(self, node: ast.Call, name: str) -> int:
        """`host_file_kind(path, n)` from code with no static types.

        THE SAME ONE `Op.CALL` the static path emits, with a conversion either
        side of it -- the arrangement `_dyn_ctypes_call` already uses, and
        simpler here because every host service answers `i64` and takes only
        `i64` and `ptr`. There are no float widths and no `void` to special
        case.

        A POINTER ARGUMENT IS A STRING'S BYTES, checked at run time by
        `apy_str_bytes` because a dynamic value's kind is a run-time fact.
        That accessor already admits `str`, `bytes`, `bytearray` and an int
        meaning an address, which is every shape a path or a buffer arrives
        in.
        """
        from ...objects import hostsvc as _hostsvc

        params, _ = _hostsvc.ALL[name]
        args = []
        for arg, want in zip(node.args, params):
            value = self._dyn_expr(arg)
            if want == "ptr":
                args.append(self.b.call(T.PTR, "apy_str_bytes", [value]))
            else:
                args.append(self.b.call(T.I64, "apy_as_int", [value]))
            self._dyn_check()
        out = self.b.call(T.I64, name, args)
        self._dyn_check()
        return self.b.call(T.PTR, "apy_from_int", [out])

    def _dyn_call(self, node: ast.Call) -> int:
        # `getattr` AND NOT `self.info.ctypes_calls`. A comprehension and a
        # lambda are lowered against a `_Synthetic` info that carries only
        # what those need, so reaching for a real `FunctionInfo` field here
        # raised AttributeError from inside the compiler -- 28 tests, none of
        # them about ctypes.
        table = getattr(self.info, "ctypes_calls", None)
        native = table.get(id(node)) if table else None
        if native is not None:
            return self._dyn_ctypes_call(node, native)
        if isinstance(node.func, ast.Name) and node.func.id in _HOSTSVC_NAMES \
                and self.info.locals.get(node.func.id) is None:
            # A HOST SERVICE. Recognised by NAME rather than recorded by the
            # analyser, because the set is closed and known at import -- there
            # is nothing per-call to remember, which is the difference from
            # `ctypes` where the signature comes from the program's own
            # `argtypes`.
            return self._dyn_hostsvc_call(node, node.func.id)
        if (isinstance(node.func, ast.Name) and node.func.id == "dict"
                and not node.args and node.keywords
                and "dict" not in self.info.locals):
            # `dict(a=1, **other)` IS THE KEYWORD MAPPING, built here.
            #
            # There is no thunk shape for a builtin that takes `**kw`: the
            # value form of a builtin collects POSITIONAL arguments, so the
            # call went through one and reported `dict() got an unexpected
            # keyword argument`. In SOURCE ORDER, so a later key wins over one
            # a `**` brought -- `dict(**d, k=1)` and `dict(k=1, **d)` are
            # different dicts and CPython keeps the difference.
            out = self.b.call(T.PTR, "apy_dict_new",
                              [self.b.const(T.I64, len(node.keywords) + 1)])
            for kw in node.keywords:
                if kw.arg is None:
                    self.b.call(T.PTR, "apy_update",
                                [out, self._dyn_expr(kw.value)])
                else:
                    self.b.call(T.PTR, "apy_dict_set",
                                [out, self._dyn_str_literal(kw.arg),
                                 self._dyn_expr(kw.value)])
                self._dyn_check()
            return out
        if any(isinstance(a, ast.Starred) for a in node.args):
            # `f(*xs)`. The argument COUNT is a value, so this cannot be a
            # direct call with a fixed IR arity -- the callee is evaluated as a
            # VALUE and the arguments are spread from a list built at run time.
            if isinstance(node.func, ast.Name) and node.func.id == "print"                     and self.info.locals.get("print") is None                     and any(kw.arg in ("sep", "end") for kw in node.keywords):
                # `print(*xs, sep=",")`. The starred form builds its arguments
                # at run time, so it cannot use the stack-array entry point --
                # and the generic spread call has nowhere to put `sep`, which
                # it therefore DROPPED rather than refusing.
                named = {kw.arg: kw.value for kw in node.keywords if kw.arg}

                def given(which):
                    return (self._dyn_expr(named[which]) if which in named
                            else self.b.call(T.PTR, "apy_none", []))

                out = self.b.call(T.PTR, "apy_print_seq_with",
                                  [self._dyn_star_args(node), given("sep"),
                                   given("end")])
                self._dyn_check()
                return out
            if (isinstance(node.func, ast.Name)
                    and node.func.id in ("max", "min")
                    and len(node.args) == 1 and not node.keywords
                    and self.info.locals.get(node.func.id) is None):
                # `max(*xs)` IS `max(xs)`: both ask for the largest of these,
                # and the answer cannot differ, because `max(a, b, c)` is
                # defined as the largest of `[a, b, c]`.
                #
                # THE SPREAD PATH CANNOT ANSWER IT. It reaches `max` as a
                # VALUE, and as a value it is a one-argument thunk that scans
                # an iterable -- so binding two spread arguments to it handed
                # the scan an int and raised "'int' object is not iterable"
                # for a call CPython answers.
                out = self.b.call(T.PTR, DYN_UNARY_BUILTIN[node.func.id],
                                  [self._dyn_expr(node.args[0].value)])
                self._dyn_check()
                return out
            return self._dyn_spread_call(node)
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and (node.func.value.id, node.func.attr) in _TYPE_STATICS \
                and self.info.locals.get(node.func.value.id) is None:
            # `dict.fromkeys(...)` -- a constructor on the TYPE, with no
            # receiver. See `_TYPE_STATICS`.
            symbol, arity, defaults = _TYPE_STATICS[
                (node.func.value.id, node.func.attr)]
            args = self._dyn_operands(node.args[:arity])
            while len(args) < arity:
                filler = defaults[len(args) - len(node.args)] \
                    if len(args) - len(node.args) < len(defaults) else "apy_none"
                args.append(self.b.call(T.PTR, filler, []))
            if symbol == "apy_bytes_fromhex":
                # Its runtime shape is (receiver, text), shared with the
                # method form; the type has no receiver to give.
                args = [self.b.call(T.PTR, "apy_none", [])] + args
            out = self.b.call(T.PTR, symbol, args)
            self._dyn_check()
            return out
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "object" \
                and node.func.attr in OBJECT_DEFAULTS \
                and self.info.locals.get("object") is None:
            # `object.__getattribute__(self, name)` -- the DEFAULT, reached by
            # name because there is no `object` value to hold it. This is how
            # an override calls what it overrode, and the only way out of the
            # recursion `__getattribute__` would otherwise be.
            symbol, arity = OBJECT_DEFAULTS[node.func.attr]
            args = self._dyn_operands(node.args[:arity])
            while len(args) < arity:
                args.append(self.b.call(T.PTR, "apy_none", []))
            out = self.b.call(T.PTR, symbol, args)
            self._dyn_check()
            return out
        if isinstance(node.func, ast.Attribute):
            return self._dyn_method(node)
        if not isinstance(node.func, ast.Name):
            # The callee is computed: `fs[0](x)`, `make()(y)`.
            return self._dyn_indirect(self._dyn_expr(node.func),
                                      self._dyn_operands(node.args),
                                      node.keywords)
        name = node.func.id
        if name == "super" and not node.args:
            return self._dyn_super()
        if name == "super" and len(node.args) == 2:
            return self._dyn_super_explicit(node)
        info = self.infos.get(name)
        # Three shapes the DIRECT path cannot express, all of them decided by
        # something it cannot see at the call site.
        by_value = (
            # `@deco def f` binds whatever `deco` returned; calling `pyf_f`
            # would run the undecorated body -- a program that still works and
            # does the wrong thing.
            (info is not None and getattr(info.node, "decorator_list", ()))
            # `def f(**kw)` collects the keywords the signature does not name,
            # and only the runtime knows which those are.
            or (info is not None and info.kwarg)
            # `f(**d)` names its keywords with a dict that exists at run time.
            or any(kw.arg is None for kw in node.keywords)
            # A NAME THE MODULE DEFINES TWICE means whichever `def` has run,
            # and which that is depends on where the call sits. The direct
            # path picks one at compile time, so a call written between the
            # two reached the second -- a wrong answer rather than a refusal.
            or name in self.rebound
            # A PROVABLY WRONG ARGUMENT COUNT is a TypeError a program may
            # CATCH, so it has to reach the runtime. The direct path would
            # hand the symbol a count it cannot accept, which is a C compile
            # error rather than a Python one.
            or id(node) in self.late_arity)
        if name in self.exc_classes and name not in self.rebound:
            # AN EXCEPTION CLASS IS CALLED THROUGH ITS NAME, ahead of the
            # callable-value path below. The `class` statement BINDS the name
            # -- a program reads `MyError.__mro__` and hands the class to
            # `issubclass` -- and that binding makes the name a local, so
            # `AppError(404, "x")` went through `apy_call` and built an
            # INSTANCE of the type object rather than an exception. It then
            # failed at the `raise` with "must derive from BaseException",
            # naming the very class it had just been handed.
            #
            # `locals` is still consulted, because a program that rebinds the
            # name to something of its own means that instead.
            return self._dyn_exception(node)
        if name in self.class_names or by_value \
                or (name not in self.infos
                    and self._is_callable_value(name)):
            # A DECORATED function must be called through its NAME and not
            # through the direct symbol: `@deco def f` binds whatever `deco`
            # returned, and calling `pyf_f` would run the undecorated body --
            # a program that still works and does the wrong thing.
            # A class, or a local holding a callable. Both are values, and a
            # value is called through `apy_call`.
            #
            # A BUILTIN REACHED HERE NEEDS ITS THUNK. `dict(**d, b=2)` comes
            # this way because of the `**`, and `_dyn_load` has no notion of a
            # builtin as a value -- so it read a module global named `dict`,
            # which no program defines, and the IR verifier refused the
            # program over `gv_dict`.
            callee = (self._dyn_builtin_value(name)
                      if name in _VALUE_BUILTINS
                      and name not in self.info.locals
                      and name not in self.infos
                      and name not in self.class_names
                      else self._dyn_load(name))
            return self._dyn_indirect(callee, self._dyn_operands(node.args),
                                      node.keywords)
        if name == "print":
            return self._dyn_print(node)
        if name in _EXC_NAMES or name in self.exc_classes:
            return self._dyn_exception(node)
        # The one-for-one builtins: one runtime call each, arguments in
        # order. Kept as a table rather than a case apiece, because that is
        # all any of them is.
        if name in _MULTI_BUILTINS:
            out = self._dyn_multi_builtin(name, node)
            if out is not None:
                return out
        if name in ("sorted", "min", "max") and node.keywords:
            # `key=` and `reverse=`. Passed as VALUES rather than folded in,
            # so a key function is called once per element by the runtime --
            # which is where the element ordering lives.
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            keyfn = (self._dyn_expr(kw["key"]) if "key" in kw
                     else self.b.call(T.PTR, "apy_none", []))
            seq = self._dyn_expr(node.args[0])
            if name == "sorted":
                rev = (self._dyn_expr(kw["reverse"]) if "reverse" in kw
                       else self.b.call(T.PTR, "apy_from_bool",
                                        [self.b.const(T.I64, 0)]))
                out = self.b.call(T.PTR, "apy_sorted_by", [seq, keyfn, rev])
            else:
                out = self.b.call(T.PTR, f"apy_{name}_by", [seq, keyfn])
            self._dyn_check()
            return out
        if name == "dict":
            # `dict()` is empty; `dict(pairs)` fills from a sequence of pairs.
            out = self.b.call(T.PTR, "apy_dict_new",
                              [self.b.const(T.I64, 1)])
            if node.args:
                out = self.b.call(T.PTR, "apy_to_dict",
                                  [self._dyn_expr(node.args[0])])
                self._dyn_check()
            return out
        if name == "type" and len(node.args) == 3:
            # `type(name, bases, ns)` MAKES a class -- the `class` statement
            # written out. One argument ASKS, and goes the ordinary way.
            out = self.b.call(T.PTR, "apy_type_make",
                              self._dyn_operands(node.args))
            self._dyn_check()
            return out
        if name == "dir" and not node.args:
            # `dir()` WITH NO ARGUMENT is the names in scope, sorted -- which
            # is `sorted(locals())`, and now that `locals()` exists there is
            # nothing else to build.
            out = self.b.call(T.PTR, "apy_sorted",
                              [self._dyn_scope_dict("locals")])
            self._dyn_check()
            return out
        if name in ("locals", "globals"):
            return self._dyn_scope_dict(name)
        if name == "bytearray":
            # `bytearray()` is empty, and the zero-argument case goes through
            # the same call with an empty list rather than a second entry
            # point -- an empty sequence of octets IS what it means.
            arg = (self._dyn_expr(node.args[0]) if node.args
                   else self.b.call(T.PTR, "apy_list_new",
                                    [self.b.const(T.I64, 1)]))
            out = self.b.call(T.PTR, "apy_to_bytearray", [arg])
            self._dyn_check()
            return out
        if name == "memoryview":
            out = self.b.call(T.PTR, "apy_memoryview",
                              [self._dyn_expr(node.args[0])])
            self._dyn_check()
            return out
        if name == "bytes":
            # `bytes()` is empty; `bytes(xs)` takes a sequence of octets.
            if not node.args:
                return self.b.call(T.PTR, "apy_to_bytes",
                                   [self.b.call(T.PTR, "apy_list_new",
                                                [self.b.const(T.I64, 1)])])
            out = self.b.call(T.PTR, "apy_to_bytes",
                              [self._dyn_expr(node.args[0])])
            self._dyn_check()
            return out
        if name == "next":
            # `next(it)` and `next(it, default)`. The default cannot be a
            # sentinel value -- `next(it, None)` is a real call with a real
            # default -- so whether there was one travels separately.
            it = self._dyn_expr(node.args[0])
            fallback = (self._dyn_expr(node.args[1]) if len(node.args) > 1
                        else self.b.call(T.PTR, "apy_none", []))
            out = self.b.call(T.PTR, "apy_next",
                              [it, fallback,
                               self.b.const(T.I64, 1 if len(node.args) > 1
                                            else 0)])
            self._dyn_check()
            return out
        if name in _DIRECT_BUILTINS:
            out = self.b.call(T.PTR, _DIRECT_BUILTINS[name],
                              self._dyn_operands(node.args))
            self._dyn_check()
            return out
        if name == "complex":
            zero = self.b.call(T.PTR, "apy_from_complex",
                               [self.b.const(T.F64, 0.0),
                                self.b.const(T.F64, 0.0)])
            if not node.args:
                return zero
            real = self._dyn_expr(node.args[0])
            # NONE FOR "NOT GIVEN", not the integer 0. `complex(x)` asks the
            # class through `__complex__`; `complex(x, 0)` is building from
            # parts and has nothing to ask, and a 0 default made the two
            # indistinguishable.
            imag = (self._dyn_expr(node.args[1]) if len(node.args) > 1
                    else self.b.call(T.PTR, "apy_none", []))
            out = self.b.call(T.PTR, "apy_complex_of", [real, imag])
            self._dyn_check()
            return out
        if name in ("set", "frozenset"):
            # `set()` with no argument is the empty set; with one it converts.
            if not node.args:
                return self.b.call(
                    T.PTR,
                    "apy_frozenset_new" if name == "frozenset" else "apy_set_new",
                    [self.b.const(T.I64, 1)])
            out = self.b.call(
                T.PTR,
                "apy_to_frozenset" if name == "frozenset" else "apy_to_set",
                [self._dyn_expr(node.args[0])])
            self._dyn_check()
            return out
        if name in ("list", "tuple"):
            # `list()` IS `[]`. The empty call is the type's zero value, not a
            # conversion of nothing, and the conversion path reads an argument
            # that is not there.
            if not node.args:
                return self.b.call(
                    T.PTR,
                    "apy_tuple_new" if name == "tuple" else "apy_list_new",
                    [self.b.const(T.I64, 1)])
            return self._dyn_convert_sequence(node, name)
        if name == "object" and not node.args:
            # `object()` -- A BARE INSTANCE, which is what a program uses as a
            # unique sentinel: nothing else compares equal to it.
            out = self.b.call(T.PTR, "apy_instance_new",
                              [self.b.call(T.PTR, "apy_object_class", [])])
            self._dyn_check()
            return out
        if name == "bool" and not node.args:
            return self.b.call(T.PTR, "apy_from_bool",
                               [self.b.const(T.I64, 0)])
        if name == "str" and not node.args:
            return self._dyn_str_literal("")
        if name == "float" and not node.args:
            return self.b.call(T.PTR, "apy_from_float",
                               [self.b.const(T.F64, 0.0)])
        if name == "isinstance":
            # The second argument NAMES a type. For a user class that name is
            # a value and travels as the class itself, so two classes both
            # called `Node` are not instances of each other; for a builtin
            # there is no such value and the name travels as text, which is
            # what makes `isinstance(True, int)` answer True.
            subject = self._dyn_expr(node.args[0])
            spec = node.args[1]
            if isinstance(spec, (ast.Tuple, ast.List)):
                # A LITERAL TUPLE OF TYPES, built as a tuple value so that it
                # takes the same runtime path as one held in a variable --
                # `isinstance(node, KINDS)` and `isinstance(node, (A, B))` are
                # the same question and should not be two implementations.
                names = self.b.call(T.PTR, "apy_tuple_new",
                                    [self.b.const(T.I64,
                                                  len(spec.elts) + 1)])
                for element in spec.elts:
                    self.b.call(T.PTR, "apy_seq_push",
                                [names, self._dyn_type_name(element)])
                out = self.b.call(T.PTR, "apy_isinstance", [subject, names])
            else:
                out = self.b.call(T.PTR, "apy_isinstance",
                                  [subject, self._dyn_type_name(spec)])
            self._dyn_check()
            return out
        if name == "enumerate":
            named = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            start = (self._dyn_expr(node.args[1]) if len(node.args) > 1
                     else self._dyn_expr(named["start"]) if "start" in named
                     else None)
            seq = self._dyn_expr(node.args[0])
            raw = (self.b.call(T.I64, "apy_index", [start]) if start is not None
                   else self.b.const(T.I64, 0))
            out = self.b.call(T.PTR, "apy_enumerate", [seq, raw])
            self._dyn_check()
            return out
        if name == "range":
            # `range` used as a value. A `for` header never reaches here --
            # `_dyn_for_range` lowers that to a counter with no list at all.
            raw = [self.b.call(T.I64, "apy_index", [self._dyn_expr(a)])
                   for a in node.args]
            while len(raw) < 2:
                raw.insert(0, self.b.const(T.I64, 0))
            if len(raw) < 3:
                raw.append(self.b.const(T.I64, 1))
            out = self.b.call(T.PTR, "apy_range", raw)
            self._dyn_check()
            return out
        if name == "zip":
            out = self.b.call(T.PTR, "apy_zip2",
                              [self._dyn_expr(node.args[0]),
                               self._dyn_expr(node.args[1])])
            self._dyn_check()
            return out
        if name in DYN_UNARY_BUILTIN:
            out = self.b.call(T.PTR, DYN_UNARY_BUILTIN[name],
                              [self._dyn_expr(node.args[0])])
            self._dyn_check()
            return out
        info = self.infos[name]
        args = (self._dyn_arguments(node, info) if info.dynamic
                else self._dyn_operands(node.args))
        if info.dynamic:
            # The direct path, kept for a module-level `def` whose identity is
            # known here: no array to build and no arity check at run time,
            # and keyword arguments and defaults are resolved at compile time
            # by `_dyn_arguments`. It still passes an ENV, because every
            # dynamic function takes one -- a module-level `def` captures
            # nothing, so None is a truthful environment and the callee never
            # reads it.
            env = self.b.call(T.PTR, "apy_none", [])
            out = self.b.call(T.PTR, self.symbols[name], [env, *args])
            # A CALLED FUNCTION CAN RAISE, and a direct call is still a call:
            # it leaves with the flag set and a null result, exactly as an
            # `apy_add` does. Without this check the null flowed on into the
            # next operation and the handler in this frame never ran.
            self._dyn_check()
            return out if out is not None else self.b.call(T.PTR, "apy_none", [])
        # A dynamic function calling a statically typed one. Each argument is
        # unwrapped to the machine type that function declared and the result
        # wrapped back. THE boundary between the two representations, and the
        # only one -- everywhere else a value is one or the other for its
        # whole life.
        params, ret = info.signature
        raw = [self._dyn_unwrap(v, want) for v, want in zip(args, params)]
        return self._dyn_wrap(self.b.call(TO_IR[ret], self.symbols[name], raw),
                              ret)

    def _dyn_unwrap(self, value: int, want: SemType) -> int:
        if want is FLOAT:
            return self.b.call(T.F64, "apy_as_float", [value])
        if want is BOOL:
            wide = self.b.call(T.I64, "apy_as_bool", [value])
            narrow = self.b.reg(T.I1)
            self.b.emit(Instruction(Op.TRUNC, T.I1, dst=narrow, args=[wide]))
            return narrow
        return self.b.call(T.I64, "apy_as_int", [value])

    def _dyn_wrap(self, value: int | None, ty: SemType) -> int:
        if ty is NONE or value is None:
            return self.b.call(T.PTR, "apy_none", [])
        if ty is FLOAT:
            return self.b.call(T.PTR, "apy_from_float", [value])
        if ty is BOOL:
            wide = self.b.reg(T.I64)
            self.b.emit(Instruction(Op.EXTEND, T.I64, dst=wide, args=[value]))
            return self.b.call(T.PTR, "apy_from_bool", [wide])
        return self.b.call(T.PTR, "apy_from_int", [value])

    def _dyn_print(self, node: ast.Call) -> int:
        """`print(a, b)` -- ONE call, with the values in a stack array.

        Not one call per argument: `print` puts a space between arguments and a
        newline after the last, and `print()` is an empty line. A per-argument
        primitive cannot express either without the caller already knowing the
        count, so the count is what it is given.
        """
        values = self._dyn_operands(node.args)
        buf = self.b.alloca(max(1, len(values)) * 8)
        for i, value in enumerate(values):
            # `offset` takes a REGISTER holding the byte displacement, not a
            # Python int -- the IR has no immediate form for it.
            slot = buf if i == 0 else self.b.offset(buf,
                                                    self.b.const(T.I64, i * 8))
            self.b.store(T.PTR, value, slot)
        named = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if "sep" in named or "end" in named:
            # `sep=` and `end=` travel as VALUES: they are ordinary keyword
            # arguments and a program may compute them. An omitted one is None,
            # which the runtime reads as "the default" -- so `end=None` and no
            # `end` are the same request.
            def given(which):
                return (self._dyn_expr(named[which]) if which in named
                        else self.b.call(T.PTR, "apy_none", []))

            self.b.call(T.VOID, "apy_print_with",
                        [buf, self.b.const(T.I64, len(values)),
                         given("sep"), given("end")])
        else:
            self.b.call(T.VOID, "apy_print",
                        [buf, self.b.const(T.I64, len(values))])
        return self.b.call(T.PTR, "apy_none", [])

    def _dyn_for_sequence(self, node: ast.For, name: str) -> None:
        """`for x in <anything iterable>`, ADVANCED UNTIL DONE.

        Not walked by index. The index walk read the length once and then
        asked for 0, 1, 2..., which is simple and wrong in two ways no care at
        this call site could fix: a generator has no length until it has been
        run, so iterating one had to drain it and laziness was impossible; and
        a body that appends to the list it is walking saw the length from
        before, where CPython sees the new elements.

        `apy_step` answers the sentinel `apy_stop()` at the end -- a cell and
        not a null, because null already means "an error is set" and running
        out is not an error.
        """
        cursor = self._keep(self._dyn_iterator(node.iter))
        test = self.b.new_block("fortest")
        body = self.b.new_block("forbody")
        done = self.b.new_block("forend")
        broke = self.b.new_block("forbroke") if node.orelse else done
        self.b.jump(test)

        self.b.switch_to(test)
        item = self.b.call(T.PTR, "apy_step", [cursor()])
        self._dyn_check()
        held = self._keep(item)
        self.b.branch(self.b.cmp(Op.EQ, T.PTR, item,
                                 self.b.call(T.PTR, "apy_stop", [])),
                      done, body)

        self.b.switch_to(body)
        if isinstance(node.target, (ast.Tuple, ast.List)):
            self._dyn_unpack(node.target, held())
        else:
            self._dyn_store(name, held())
        self.loops.append((test, broke, len(self.finallys)))
        self._dyn_stmts(node.body)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(test)

        self.b.switch_to(done)
        self._dyn_loop_else(node, broke)

    def _dyn_async_for(self, node) -> None:
        """`async for v in agen` -- step an async generator, suspending with it.

        THREE OUTCOMES FROM ONE STEP, which is what makes this its own loop
        rather than a flag on the ordinary one: the generator produced an
        item, or it suspended on an `await` inside itself, or it is exhausted.
        A suspension has to become a suspension of THIS coroutine and then
        resume the same step -- the item never arrived, so the loop must not
        advance.

        The awaited object lives in a frame slot for the reason everything
        else here does: the loop suspends, and a register does not survive
        the return a suspension compiles to.
        """
        at_src = self._gen_temp()
        # THROUGH `__aiter__`, which is the protocol. An async generator
        # answers itself, so this changes nothing for one -- and a CLASS
        # implementing `__aiter__`/`__anext__` becomes iterable at all, which
        # it was not: the step went straight to the async-generator machinery
        # and reported the class as not supporting asynchronous iteration.
        started = self.b.call(T.PTR, "apy_aiter", [self._dyn_expr(node.iter)])
        self._dyn_check()
        self._gen_put(at_src, started)
        test = self.b.new_block("afortest")
        suspended = self.b.new_block("aforsuspend")
        got_item = self.b.new_block("aforitem")
        body = self.b.new_block("aforbody")
        done = self.b.new_block("aforend")
        broke = self.b.new_block("aforbroke") if node.orelse else done
        self.b.jump(test)

        self.b.switch_to(test)
        stepped = self.b.call(T.PTR, "apy_agen_step", [self._gen_get(at_src)])
        self._dyn_check()
        at_item = self._gen_temp()
        self._gen_put(at_item, stepped)
        self.b.branch(self.b.cmp(Op.EQ, T.PTR, stepped,
                                 self.b.call(T.PTR, "apy_stop", [])),
                      done, got_item)

        self.b.switch_to(got_item)
        self.b.branch(self.b.cmp(Op.EQ, T.PTR, self._gen_get(at_item),
                                 self.b.call(T.PTR, "apy_suspend_value", [])),
                      suspended, body)

        # Pass the suspension outward, then RETRY THE SAME STEP: the awaited
        # thing inside the generator has not finished, so no item was produced.
        self.b.switch_to(suspended)
        self._dyn_yield_value(self._gen_get(at_item))
        self.b.jump(test)

        self.b.switch_to(body)
        if isinstance(node.target, (ast.Tuple, ast.List)):
            self._dyn_unpack(node.target, self._gen_get(at_item))
        else:
            self._dyn_store(node.target.id, self._gen_get(at_item))
        self.loops.append((test, broke, len(self.finallys)))
        self._dyn_stmts(node.body)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(test)

        self.b.switch_to(done)
        self._dyn_loop_else(node, broke)

    def _dyn_iterator(self, node) -> int:
        """What to step. One call, at the top of the loop, evaluating the
        iterable exactly once -- which is what makes `for x in f()` call `f`
        a single time."""
        got = self.b.call(T.PTR, "apy_getiter", [self._dyn_expr(node)])
        self._dyn_check()
        return got

    # ── statements ──────────────────────────────────────────────────────────
    #: Attributes that only a program reading a TRACEBACK writes. Recording
    #: positions costs a call per statement, so it happens for a program that
    #: mentions one of these and for no other -- which is the difference
    #: between a feature that is free when unused and one that is not.
    _TRACEBACK_NAMES = ("__traceback__", "tb_frame", "tb_lineno", "tb_next",
                        "tb_lasti", "co_positions", "f_lineno")

    @property
    def _wants_positions(self) -> bool:
        """Whether this program looks at a traceback at all.

        Asked of the SOURCE TEXT rather than the tree, because the question
        is "does the word appear anywhere", which is what a text search
        answers exactly -- an attribute reached through `getattr(e,
        "__traceback__")` counts, and no tree walk short of evaluating the
        program would find it.

        WHAT IT DOES NOT SEE is a BUNDLED module: the splicer inserts a tree,
        not text, so a bundled module reading a traceback would not turn this
        on and would get the empty-tuple stand-in. None of them does -- it is
        checked, not assumed -- and the day one wants to, this has to look at
        the spliced tree as well.
        """
        if self._positions_wanted is None:
            text = getattr(self.source, "text", "") or ""
            self._positions_wanted = any(one in text
                                         for one in self._TRACEBACK_NAMES)
        return self._positions_wanted

    def _dyn_record_position(self, node) -> None:
        """Record where this statement was written, and say so at run time.

        ONE PER STATEMENT is the granularity the frontend has: `_dyn_stmt`
        sets a span per statement and nothing finer is tracked. That is what
        `co_positions()` answers with, and it is coarser than CPython's
        per-instruction table rather than different in kind.
        """
        if not self._wants_positions or self.b.current.terminator is not None:
            return
        line = getattr(node, "lineno", 0)
        self._positions.append(
            (self.info.name, line, getattr(node, "end_lineno", line) or line,
             getattr(node, "col_offset", 0),
             getattr(node, "end_col_offset", 0) or 0))
        self.b.call(T.VOID, "apy_at",
                    [self.b.const(T.I64, len(self._positions) - 1)])

    def _dyn_emit_positions(self) -> None:
        """The function that fills the position table, emitted last.

        A function rather than a prologue in the entry, because the rows are
        discovered WHILE the entry and everything else is lowered -- so the
        entry calls this by name and the definition arrives afterwards, which
        is what a symbol-keyed IR call allows.
        """
        if not self._positions:
            return
        fn = Function("pyf__positions", T.VOID, linkage=Linkage.INTERNAL)
        was_b, was_fn = self.b, self.fn
        self.fn = fn
        self.b = Builder(fn)
        self.b.switch_to(self.b.new_block("entry"))
        for name, line, end_line, col, end_col in self._positions:
            self.b.call(T.VOID, "apy_pos_add",
                        [self._dyn_str_literal(name),
                         self.b.const(T.I64, line),
                         self.b.const(T.I64, end_line),
                         self.b.const(T.I64, col),
                         self.b.const(T.I64, end_col)])
        self.b.ret(None)
        self.module.functions.append(fn)
        self.b, self.fn = was_b, was_fn

    def _dyn_stmts(self, body: list) -> None:
        for stmt in body:
            if self.b.current.terminator is not None:
                return          # dead code after return/break/continue
            self._dyn_stmt(stmt)

    def _dyn_stmt(self, node) -> None:
        self.b.span = self._span(node)
        self._dyn_record_position(node)
        match node:
            case _ if id(node) in self.ctypes_stmts:
                # A CTYPES DECLARATION DESCRIBES THE BUILD. `libm =
                # ctypes.CDLL("m")` names a library for the linker and
                # `libm.sqrt.restype = ...` names a signature for the caller;
                # neither does anything at run time, and lowering them as
                # ordinary statements looked for a `ctypes` that was never
                # going to exist -- `NameError: name 'ctypes' is not defined`,
                # from a program whose every native call had already compiled.
                #
                # FIRST IN THE MATCH, because `case ast.Assign(...)` matches
                # the same statements and comes below: a guard placed after it
                # never runs.
                pass
            case ast.Expr():
                if not isinstance(node.value, ast.Constant):
                    self._dyn_expr(node.value)
            case ast.Assign(targets=[_, _, *_]):
                # `a = b = value` -- ONE evaluation, bound to each target left
                # to right, which is what makes `a = b = []` two names for the
                # same list.
                built = self._dyn_expr(node.value)
                for target in node.targets:
                    self._dyn_unpack(target, built)
            case ast.Assign(targets=[ast.Name(id=name)]):
                self._dyn_bind(name, node.value)
            case ast.Assign(targets=[(ast.Tuple() | ast.List()) as target]):
                self._dyn_unpack(target, self._dyn_expr(node.value))
            case ast.Assign(targets=[ast.Subscript() as target]):
                # THE RIGHT-HAND SIDE FIRST. `s[probe("key")] = probe("value")`
                # calls `probe("value")` before `probe("key")`, which is
                # Python's rule for an assignment and the opposite of the
                # reading order. Evaluating the target first got the right
                # answer with the wrong side effects.
                value = self._dyn_expr(node.value)
                container = self._dyn_expr(target.value)
                index = self._dyn_expr(target.slice)
                self.b.call(T.PTR, "apy_setitem", [container, index, value])
                self._dyn_check()
            case ast.Assign(targets=[ast.Attribute() as target]):
                # THE RIGHT-HAND SIDE FIRST, here too: `f().x = g()` calls `g`
                # before `f`. An assignment evaluates what it is assigning
                # before where it is putting it, which is the opposite of the
                # reading order and is why it is worth stating.
                value = self._dyn_expr(node.value)
                obj = self._dyn_expr(target.value)
                self.b.call(T.PTR, "apy_setattr",
                            [obj, self._dyn_attr_literal(target.attr), value])
                self._dyn_check()
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                # PEP 695: `def first[T](...)`. The parameters are bound
                # BEFORE the definition, because its annotations name them and
                # the thunk that builds those reads them as globals.
                made = self._dyn_type_params(node)
                # An `async def` binds its name exactly as a `def` does. What
                # differs is inside: the function it binds builds a coroutine
                # rather than running the body. See `_dyn_generator`.
                #
                # A STATICALLY TYPED `def` has no storage to bind: it is
                # reached by direct call only, and its name is not a module
                # name. The statement still exists -- module-level `def`s stay
                # in the entry's body so their defaults run in source order --
                # and for that shape there is simply nothing to do.
                key = self.def_keys.get(id(node))
                if key is None or not self.infos[key].dynamic:
                    pass
                elif (self._is_module_name(node.name)
                        or node.name in self.info.locals):
                    self._dyn_store(node.name, self._dyn_decorated(
                        node, self._dyn_function_value(key, node.name)))
                    self._dyn_record_type_params(node, made)
            case ast.ClassDef():
                made = self._dyn_type_params(node)
                self._dyn_class(node)
                self._dyn_record_type_params(node, made)
            case ast.AnnAssign(target=ast.Name(id=name)):
                if node.value is not None:
                    self._dyn_bind(name, node.value)
            case ast.TypeAlias():
                # PEP 695: `type Alias = list[int]`.
                #
                # THE TYPE PARAMETERS ARE SUBSTITUTED INTO THE VALUE rather
                # than bound as names: they are in scope for the value alone,
                # and binding them as locals would leak `T` into the rest of
                # the function. Rewriting each mention to the object it stands
                # for gives the same value with nothing left behind.
                params = [one.name for one in node.type_params]
                made = [self.b.call(T.PTR, "apy_typevar",
                                    [self._dyn_str_literal(one)])
                        for one in params]
                held = dict(zip(params, made))
                value = self._dyn_expr(_TypeParams(held).visit(node.value))                     if held else self._dyn_expr(node.value)
                tup = self.b.call(T.PTR, "apy_tuple_new",
                                  [self.b.const(T.I64, len(made) + 1)])
                for one in made:
                    self.b.call(T.PTR, "apy_seq_push", [tup, one])
                self._dyn_store(node.name.id, self.b.call(
                    T.PTR, "apy_type_alias",
                    [self._dyn_str_literal(node.name.id), value, tup]))
                self._dyn_check()
            case ast.AnnAssign(target=(ast.Attribute() | ast.Subscript())):
                # `self.items: list[T] = []` -- the annotation says nothing
                # the runtime keeps, so what is left is the assignment, and
                # the plain form of it is already lowered above. Rewritten
                # rather than reimplemented, so the two cannot drift.
                if node.value is not None:
                    self._dyn_stmt(ast.copy_location(
                        ast.Assign(targets=[node.target], value=node.value),
                        node))
            case ast.AugAssign(target=ast.Subscript()):
                # `xs[i] += v`. The CONTAINER AND THE INDEX ARE EVALUATED
                # ONCE, then read, combined and written back -- so
                # `xs[idx()] += 5` calls `idx` a single time. Rewriting to
                # `xs[i] = xs[i] + v` would call it twice, which is
                # observable and is exactly what the suite checks.
                target = self._dyn_expr(node.target.value)
                index = self._dyn_expr(node.target.slice)
                current = self.b.call(T.PTR, "apy_getitem", [target, index])
                self._dyn_check()
                combined = self._dyn_inplace(node, current)
                self.b.call(T.PTR, "apy_setitem", [target, index, combined])
                self._dyn_check()
            case ast.AugAssign(target=ast.Attribute()):
                obj = self._dyn_expr(node.target.value)
                name_v = self._dyn_attr_literal(node.target.attr)
                current = self.b.call(T.PTR, "apy_getattr", [obj, name_v])
                self._dyn_check()
                combined = self._dyn_inplace(node, current)
                self.b.call(T.PTR, "apy_setattr", [obj, name_v, combined])
                self._dyn_check()
            case ast.AugAssign(target=ast.Name(id=name)) if self.info.dynamic:
                # `x += y` is NOT `x = x + y` for a mutable container: a list
                # extends itself, so every other name bound to it sees the
                # change. `_dyn_inplace` picks; everything else falls through
                # to the binary operator, which is what `+=` on an immutable
                # means.
                self._dyn_store(name, self._dyn_inplace(
                    node, self._dyn_load(name)))
            case ast.AugAssign(target=ast.Name(id=name)):
                self._dyn_bind(name, self.info.aug_nodes[id(node)])
            case ast.Return() if self._gen is not None:
                # In a GENERATOR a `return` ENDS THE ITERATION, and its value
                # becomes `StopIteration.value` rather than the call's result
                # -- the call already returned the generator. `yield from`
                # reads it as the delegated generator's answer, so dropping it
                # made every delegation produce None.
                if node.value is not None:
                    self.b.call(T.PTR, "apy_gen_result",
                                [self._gen[0], self._dyn_expr(node.value)])
                self._dyn_gen_finish()
            case ast.Return():
                value = (self.b.call(T.PTR, "apy_none", [])
                         if node.value is None else self._dyn_expr(node.value))
                if self._in_finally:
                    # A `return` INSIDE A `finally` DISCARDS whatever was in
                    # flight. `try: raise V() finally: return x` answers x in
                    # CPython and raised here -- the finally body ran, decided
                    # the function's answer, and the exception it was cleaning
                    # up after went on propagating anyway.
                    self.b.call(T.VOID, "apy_error_clear", [])
                if self.finallys and self.info.dynamic:
                    # SNAPSHOT IT. The value is computed before the `finally`
                    # runs -- `try: return n finally: n = 99` answers 1 -- and
                    # a local lives in one register for its whole life, so the
                    # finally's assignment would otherwise overwrite the very
                    # register the `ret` reads. Copying costs one instruction
                    # and only where a `finally` is actually pending.
                    kept = self.b.reg(T.PTR)
                    self.b.emit(Instruction(Op.COPY, T.PTR, dst=kept,
                                            args=[value]))
                    value = kept
                # Every enclosing `finally` runs BEFORE the return, innermost
                # first -- which is what `finally` means, and what makes both
                # of its edge cases fall out rather than needing their own
                # handling:
                #
                #   * a `finally` that RETURNS overrides the pending value,
                #     because its own `ret` terminates the block and the one
                #     below is never emitted into it;
                #   * a `finally` that RAISES replaces the pending return for
                #     the same reason.
                #
                # Lowering the finally bodies AFTER the `ret` -- which is what
                # this did -- put them in a block that already ended, and the
                # builder refused with "block already ends in 'ret'". That was
                # a crash on a program CPython runs, and the fix is not to
                # guard the append but to emit in the right order.
                # Each `finally` is lowered with only the OUTER ones still
                # pending. Leaving the whole stack in place meant a `return`
                # inside a `finally` re-ran that same `finally`, which
                # recursed until the compiler ran out of stack and reported
                # the user's expression as too deeply nested.
                pending = self.finallys
                try:
                    for i in range(len(pending) - 1, -1, -1):
                        self.finallys = pending[:i]
                        self._dyn_unwind(pending[i])
                        if self.b.current.terminator is not None:
                            break
                finally:
                    self.finallys = pending
                if self.b.current.terminator is None:
                    self.b.ret(value)
            case ast.If():
                self._dyn_if(node)
            case ast.While():
                self._dyn_while(node)
            case ast.AsyncFor():
                self._dyn_async_for(node)
            case ast.For(target=(ast.Tuple() | ast.List())):
                self._dyn_for_unpack(node)
            case ast.For(target=ast.Name(id=name)):
                call = node.iter
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "range"):
                    self._dyn_for_range(node, name)
                else:
                    self._dyn_for_sequence(node, name)
            case ast.Raise():
                self._dyn_raise(node)
            case ast.Assert():
                self._dyn_assert(node)
            case ast.Import() if all(a.name == "ctypes" for a in node.names):
                # `import ctypes` BINDS NOTHING AT RUN TIME. It is a spelling
                # the compiler understands rather than a module it has: the
                # analyser turned every `lib.f(...)` into a direct call to an
                # external symbol, so there is no module object left to make
                # and nothing for this statement to store. See `cffi.py`.
                pass
            case ast.ImportFrom(module="ctypes", level=0):
                pass
            case ast.Import():
                for alias in node.names:
                    # `import a.b` BINDS `a` and `a.b` is an attribute of it;
                    # `import a.b as n` binds `n` to the module itself. Both
                    # are Python's rule, and the difference is why the value
                    # stored depends on whether there was an `as`.
                    made = self._dyn_module(alias.name)
                    if alias.asname:
                        self._dyn_store(alias.asname, made)
                    else:
                        head = alias.name.split(".")[0]
                        self._dyn_store(head, self._dyn_package(alias.name)
                                        if "." in alias.name else made)
            case ast.ImportFrom():
                for alias in node.names:
                    self._dyn_store(alias.asname or alias.name,
                                    self._dyn_member(node.module, alias.name))
            case ast.Match():
                self._dyn_match(node)
            case ast.AsyncWith():
                self._dyn_with(node, is_async=True)
            case ast.With():
                self._dyn_with(node)
            case ast.Try():
                self._dyn_try(node)
            case ast.TryStar():
                self._dyn_try_star(node)
            case ast.Delete():
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._dyn_unbind(target.id)
                    elif isinstance(target, ast.Attribute):
                        # `del obj.attr`. A `__delattr__` on the class, or a
                        # descriptor's `__delete__`, hangs off this -- both
                        # live in `apy_delattr` rather than here.
                        self.b.call(T.PTR, "apy_delattr",
                                    [self._dyn_expr(target.value),
                                     self._dyn_str_literal(target.attr)])
                        self._dyn_check()
                    else:
                        self.b.call(T.PTR, "apy_delitem",
                                    [self._dyn_expr(target.value),
                                     self._dyn_expr(target.slice)])
                        self._dyn_check()
            case ast.Global() | ast.Nonlocal():
                pass        # a declaration, not an action
            case ast.Pass():
                pass
            case ast.Break():
                self._dyn_leave_loop(self.loops[-1][1])
            case ast.Continue():
                self._dyn_leave_loop(self.loops[-1][0])
            case _:
                raise AssertionError(
                    f"dynamic lowering reached {type(node).__name__}; "
                    f"analysis should have rejected it")

    def _dyn_exception(self, node: ast.expr) -> int:
        """`ValueError('x')`, or a bare `ValueError`.

        A bare name is the same as calling it with no argument, which is what
        `raise ValueError` means -- CPython instantiates it for you.
        """
        if isinstance(node, ast.Name):
            # `raise e` WHERE `e` IS A VARIABLE re-raises the object it holds.
            # Only a name that IS an exception type means "make one of these"
            # -- treating every name that way built an exception named after
            # the variable, so `raise e` reported `e:` and the handler for the
            # real type never fired.
            if (node.id not in _EXC_NAMES and node.id not in self.exc_classes):
                return self._dyn_expr(node)
            name, args = node.id, []
        elif not isinstance(node, ast.Call):
            # ANYTHING ELSE IS JUST AN EXPRESSION. `raise 5` is legal to
            # write and is a TypeError at run time, which `apy_raise` words
            # -- but reaching for `node.func` on a Constant crashed the
            # COMPILER, which is a worse answer than any diagnostic.
            return self._dyn_expr(node)
        else:
            # `raise make_error()` RAISES WHAT THE CALL ANSWERS. Only a call
            # whose callee is an exception NAME means "build one of these" --
            # treating every call that way built an exception named after the
            # FUNCTION, so a factory returning a real SyntaxError was raised
            # as `make_error` and no handler for it ever fired.
            callee = getattr(node.func, "id", None)
            if callee is None or (callee not in _EXC_NAMES
                                  and callee not in self.exc_classes):
                return self._dyn_expr(node)
            name, args = callee, node.args
        # `E()` and `E(None)` are DIFFERENT exceptions -- `e.args` is `()` for
        # the first and `(None,)` for the second -- so the two go to different
        # constructors rather than both passing None.
        # A GROUP TAKES TWO -- the message and what it carries -- and has its
        # own constructor because the second argument is not a message.
        if name in ("ExceptionGroup", "BaseExceptionGroup") and len(args) == 2:
            out = self.b.call(T.PTR, "apy_excgroup_new",
                              [self._dyn_expr(args[0]),
                               self._dyn_expr(args[1])])
            self._dyn_check()
            return out
        if not args:
            out = self.b.call(T.PTR, "apy_make_exc0",
                              [self._dyn_str_literal(name)])
            # A USER `__init__` MAY RAISE, and then this answers nothing --
            # which the `raise` after it would read as an exception value.
            self._dyn_check_value(out)
            return out
        if len(args) > 1:
            # `OSError(2, "No such file")` CARRIES BOTH. One argument was all
            # the constructor took, so the rest were silently dropped and
            # `e.args` reported a one-element tuple for a two-argument raise.
            out = self.b.call(T.PTR, "apy_make_excn",
                              [self._dyn_str_literal(name),
                               self._dyn_argv([self._dyn_expr(a)
                                               for a in args]),
                               self.b.const(T.I64, len(args))])
            self._dyn_check_value(out)
            return out
        out = self.b.call(T.PTR, "apy_make_exc",
                          [self._dyn_str_literal(name),
                           self._dyn_expr(args[0])])
        self._dyn_check_value(out)
        return out

    def _dyn_raise(self, node: ast.Raise) -> None:
        if node.exc is None:
            # A bare `raise` RE-RAISES WHAT THIS HANDLER CAUGHT. The flag was
            # cleared on the way in, so the exception has to be raised again
            # from the value the handler kept -- see `_handling`. Outside any
            # handler the flag may still be set (a `finally` re-raising on its
            # way out), and there the old behaviour is right.
            if self._handling:
                self.b.call(T.PTR, "apy_raise", [self._handling[-1]()])
            self._dyn_check_forced()
            return
        if node.cause is not None:
            # `raise X from Y`. The cause is explicit AND it suppresses the
            # implicit context -- `from None` is the form that does only the
            # suppressing, and it reaches here as the constant None.
            self.b.call(T.PTR, "apy_raise_from",
                        [self._dyn_exception(node.exc),
                         self._dyn_expr(node.cause),
                         self.b.const(T.I64, 1)])
        else:
            self.b.call(T.PTR, "apy_raise", [self._dyn_exception(node.exc)])
        self._dyn_check_forced()

    def _dyn_check_value(self, value: int) -> None:
        """Stop if the call that produced `value` failed.

        NOT `_dyn_check`, which tests the sticky FLAG. A constructor may run
        while an earlier exception is still in flight -- `try: raise A /
        finally: raise B` builds B with A's flag set, and that flag says
        nothing about whether building B worked. Testing it sent the `raise B`
        straight to the handler carrying A, so the exception the `finally`
        wrote was silently dropped and the one it was replacing came out.

        A failed call answers 0, which is never a value, so that is the
        question actually being asked here.
        """
        ok = self.b.new_block("made")
        bad = self.b.new_block("makefailed")
        self.b.branch(self.b.cmp(Op.NE, T.PTR, value,
                                 self.b.const(T.PTR, 0)), ok, bad)
        self.b.switch_to(bad)
        self._dyn_check_forced()
        if self.b.current.terminator is None:
            self.b.jump(ok)
        self.b.switch_to(ok)

    def _dyn_check_forced(self) -> None:
        """Like `_dyn_check`, but the error is known to be set.

        `raise` is not "maybe an error happened": it always transfers control,
        so the block ends here rather than continuing into unreachable code.
        """
        if not self.handlers:
            if self.info.name == ENTRY_NAME:
                self.b.call(T.VOID, "apy_fatal_if_error", [])
                return
            self._dyn_return_failure()
            return
        self.b.jump(self.handlers[-1])

    def _dyn_return_failure(self) -> None:
        """Leave this function with the error flag set.

        A NULL `apy_value`, which is the runtime's own "I failed" answer -- so
        a compiled function and an `apy_add` are indistinguishable to the
        caller's check, and there is one propagation mechanism rather than
        two. A statically typed function returns its declared zero instead;
        it cannot raise, so this is only reachable through the boundary.
        """
        if self.info.ret is OBJ or self.info.dynamic:
            self.b.ret(self.b.const(T.PTR, 0))
        else:
            self.b.ret(self.b.const(TO_IR[self.info.ret], 0))

    def _dyn_assert(self, node: ast.Assert) -> None:
        ok = self.b.new_block("assertok")
        bad = self.b.new_block("assertbad")
        self.b.branch(self._dyn_truth(node.test), ok, bad)
        self.b.switch_to(bad)
        if node.msg is not None:
            # THE MESSAGE FIRST. It may suspend, and the literal beside it in
            # the argument list would otherwise be a register computed before
            # the suspension and read after it.
            msg = self._dyn_expr(node.msg)
            made = self.b.call(T.PTR, "apy_make_exc",
                               [self._dyn_str_literal("AssertionError"), msg])
        else:
            made = self.b.call(T.PTR, "apy_make_exc0",
                               [self._dyn_str_literal("AssertionError")])
        self.b.call(T.PTR, "apy_raise", [made])
        self._dyn_check_forced()
        if self.b.current.terminator is None:
            self.b.jump(ok)
        self.b.switch_to(ok)

    def _dyn_with(self, node, is_async: bool = False) -> None:
        """`with a as x, b as y: body`, and the `async with` form.

        THE TWO DIFFER ONLY IN WHAT THE PROTOCOL IS CALLED and that each half
        answers a coroutine to be awaited -- the block structure, the
        exception dispatch and the swallowing rule are identical, which is why
        this takes a flag rather than being written twice.

        Lowered as nested `try`/`finally`-shaped regions, one per item and
        innermost last, because that is what `with` IS -- `__exit__` runs on
        every path out, and an exception in the body reaches the inner
        manager's `__exit__` before the outer one's.

        `__exit__` gets the exception's TYPE, VALUE and traceback, and a true
        return SWALLOWS it. Both matter: a manager that logs `et.__name__`
        needs the type, and one that returns True has to stop the propagation
        rather than merely observe it.
        """
        if not node.items:
            self._dyn_stmts(node.body)
            return
        item, rest = node.items[0], node.items[1:]
        # THE MANAGER MUST SURVIVE THE BODY, and the body may `yield` -- so in
        # a generator it goes to a frame slot rather than staying in a
        # register that a suspension would lose.
        held = self._keep(self._dyn_expr(item.context_expr))
        manager = held()
        entered = self.b.call(T.PTR, "apy_aenter" if is_async else "apy_enter",
                              [manager])
        self._dyn_check()
        if is_async:
            entered = self._dyn_await_value(entered)
        if item.optional_vars is not None:
            self._dyn_store(item.optional_vars.id, entered)

        dispatch = self.b.new_block("withexc")
        done = self.b.new_block("withend")

        # Where `_dyn_check` should send a failure raised BY the cleanup: out
        # of this `with`, not into its own dispatch. Otherwise an `__exit__`
        # that raises while a `return` unwinds would be entered a second time
        # to handle its own exception.
        outer = len(self.handlers)

        def leave() -> None:
            """`__exit__(None, None, None)`, with its answer discarded --
            there is no exception for a true return to swallow."""
            none = self.b.call(T.PTR, "apy_none", [])
            left = self.b.call(T.PTR, "apy_aexit" if is_async else "apy_exit",
                               [held(), none])
            if is_async:
                self._dyn_await_value(left)
            saved, self.handlers = self.handlers, self.handlers[:outer]
            try:
                self._dyn_check()
            finally:
                self.handlers = saved

        self.handlers.append(dispatch.label)
        # A `return` out of the body must run `__exit__` on its way, which is
        # the same obligation `finally` has -- so it rides the same stack.
        self.finallys.append(leave)
        if rest:
            # An `ast.With` whichever form this is: the node is only a
            # carrier for the remaining items, and `is_async` -- not the node
            # class -- is what decides which protocol they use.
            inner = ast.With(items=rest, body=node.body)
            ast.copy_location(inner, node)
            self._dyn_with(inner, is_async)
        else:
            self._dyn_stmts(node.body)
        self.finallys.pop()
        self.handlers.pop()

        # The ordinary fall-through exit emits its own copy, for the same
        # reason `_dyn_try` does: the cleanup is duplicated per path out
        # rather than jumped to.
        if self.b.current.terminator is None:
            leave()
            if self.b.current.terminator is None:
                self.b.jump(done)

        # The exceptional exit: hand `__exit__` the live exception, and
        # re-raise unless it answered true.
        self.b.switch_to(dispatch)
        # IN FRAME SLOTS FOR THE ASYNC FORM, because `await __aexit__(...)`
        # SUSPENDS between writing these and reading them again -- and a
        # register does not survive the return a suspension compiles to. The
        # synchronous form never leaves this block, so it keeps its registers.
        hold = self._keep if is_async else (lambda v: (lambda: v))
        at_exc = hold(self.b.call(T.PTR, "apy_error_value", []))
        self.b.call(T.VOID, "apy_error_clear", [])
        # The original is WHAT IS BEING HANDLED while `__exit__` runs, so an
        # exception raised inside it chains to this one -- which is what
        # `e.__context__` reports and the only way to tell the two apart.
        at_was = hold(self.b.call(T.PTR, "apy_error_handling", [at_exc()]))
        swallowed = self.b.call(
            T.PTR, "apy_aexit" if is_async else "apy_exit",
            [held(), at_exc()])
        if is_async:
            swallowed = self._dyn_await_value(swallowed)
        self.b.call(T.PTR, "apy_error_handling", [at_was()])
        self._dyn_check()
        keep = self.b.new_block("withreraise")
        self.b.branch(self.b.cmp(Op.NE, T.I64,
                                 self.b.call(T.I64, "apy_truth", [swallowed]),
                                 self.b.const(T.I64, 0)),
                      done, keep)
        self.b.switch_to(keep)
        self.b.call(T.PTR, "apy_raise", [at_exc()])
        self._dyn_check_forced()
        if self.b.current.terminator is None:
            self.b.jump(done)
        self.b.switch_to(done)

    def _dyn_try(self, node: ast.Try) -> None:
        """try / except / else / finally, over the sticky error flag.

        The body runs with `self.handlers` naming the dispatch block, so every
        `_dyn_check` inside it branches there instead of exiting. Dispatch then
        tests the handlers in source order -- first match wins, as in Python --
        and clears the flag before running one, because a handler that itself
        calls a fallible operation must not see the error it just caught.

        `finally` is emitted TWICE, once on each path out. That is duplication,
        and the alternative is a return address in a register and an indirect
        jump, which the IR can express but which makes the block graph much
        harder to read for a construct that is nearly always small.
        """
        dispatch = self.b.new_block("except")
        done = self.b.new_block("tryend")
        # `finally` bodies enclosing the code being lowered, innermost last.
        # A `return` walks this; the ordinary fall-through paths emit their own
        # copy, because `finally` is duplicated per exit rather than jumped to
        # (see this method's docstring).
        self.finallys.append(node.finalbody)

        self.handlers.append(dispatch.label)
        self._dyn_stmts(node.body)
        self.handlers.pop()
        # Out of the protected region: a `return` in the HANDLERS or in
        # `finally` itself must not re-run this body.
        self.finallys.pop()
        if self.b.current.terminator is None:
            if node.orelse:
                # `else` runs only when the body finished without raising,
                # and it is NOT protected by these handlers -- an exception in
                # it propagates outward, which is the whole reason `else`
                # exists rather than putting the code at the end of `try`.
                #
                # It IS still inside the `try` statement, so a `return` in it
                # runs the `finally` on its way out; the fall-through path
                # below emits its own copy after this pops.
                self.finallys.append(node.finalbody)
                self._dyn_stmts(node.orelse)
                self.finallys.pop()
            if self.b.current.terminator is None:
                self._dyn_finally(node)
                if self.b.current.terminator is None:
                    self.b.jump(done)

        # AN EXCEPTION RAISED INSIDE A HANDLER STILL LEAVES THIS `try`, so
        # the `finally` runs on its way out. The handler bodies are lowered
        # with this statement's own handler already popped -- they are not
        # protected by their own `except` -- so without somewhere to go they
        # jumped straight to the ENCLOSING handler and skipped the `finally`
        # entirely. `try: raise / except: raise / finally: log()` lost its
        # log line.
        # MADE ONLY WHEN A HANDLER BODY ACTUALLY NEEDS IT. Creating the block
        # up front left an empty one behind for every `try`/`finally` with no
        # `except`, and the verifier rejects a block with no terminator.
        rethrow = None

        self.b.switch_to(dispatch)
        for handler in node.handlers:
            body_b = self.b.new_block("handler")
            if handler.type is None:
                nxt = None
                self.b.jump(body_b)
            else:
                nxt = self.b.new_block("nexthandler")
                matched = None
                for name in _handler_names(handler.type):
                    one = self.b.call(T.I64, "apy_error_matches",
                                      [self._dyn_str_literal(name)])
                    hit = self.b.cmp(Op.NE, T.I64, one,
                                     self.b.const(T.I64, 0))
                    if matched is None:
                        matched = hit
                    else:
                        combined = self.b.reg(T.I1)
                        self.b.emit(Instruction(Op.OR, T.I1, dst=combined,
                                                args=[matched, hit]))
                        matched = combined
                self.b.branch(matched, body_b, nxt)
            self.b.switch_to(body_b)
            # A `return` INSIDE A HANDLER still leaves the `try` statement, so
            # the `finally` runs on its way out -- `try: raise / except:
            # return "caught" / finally: log()` logs. Popped again before the
            # fall-through path below emits its own copy, so the body cannot
            # run it twice.
            self.finallys.append(node.finalbody)
            caught = self.b.call(T.PTR, "apy_error_value", [])
            if handler.name:
                self._dyn_store(handler.name, caught)
            self.b.call(T.VOID, "apy_error_clear", [])
            # WHAT IS BEING HANDLED, for implicit chaining: a `raise` in this
            # body records it as the new exception's `__context__`. Restored
            # afterwards, so a handler that finishes normally stops being the
            # context for anything raised later.
            was = self._keep(self.b.call(T.PTR, "apy_error_handling",
                                         [caught]))
            # WHAT A BARE `raise` IN THIS BODY RE-RAISES. Entering a handler
            # CLEARS the error flag -- that is what catching is -- so by the
            # time a `raise` with no argument runs there is nothing set, and
            # the old lowering jumped straight to the enclosing handler with
            # no error at all. The exception was silently swallowed and the
            # outer `except` never fired.
            self._handling.append(self._keep(caught))
            # ONLY THE HANDLER BODY is redirected through the finally. The
            # no-match arm below emits its own copy, and leaving this pushed
            # across it sent that copy here too -- the `finally` ran twice for
            # a `try`/`finally` with no `except` at all.
            if node.finalbody:
                if rethrow is None:
                    rethrow = self.b.new_block("tryfinraise")
                self.handlers.append(rethrow.label)
            self._dyn_stmts(handler.body)
            if handler.name and self.b.current.terminator is None:
                # `except ... as e` DELETES `e` when the clause ends. CPython
                # does this so the traceback the exception holds cannot keep
                # the frame alive, and a program reads the deletion back: `e`
                # after the handler is a NameError, not the caught value.
                #
                # ONLY WHERE CONTROL REACHES THE END. A handler body ending in
                # `return` or `raise` has already terminated its block, and
                # emitting into that block is invalid IR -- there is also
                # nothing to delete, because nothing follows.
                self._dyn_unbind(handler.name)
            if node.finalbody:
                self.handlers.pop()
            self._handling.pop()
            self.finallys.pop()
            if self.b.current.terminator is None:
                self.b.call(T.PTR, "apy_error_handling", [was()])
                self._dyn_finally(node)
                if self.b.current.terminator is None:
                    self.b.jump(done)
            if nxt is None:
                break
            self.b.switch_to(nxt)
        else:
            # Nothing matched. The flag is still set, so the enclosing handler
            # -- or the process -- deals with it, after `finally` has run.
            #
            # Unless `finally` TERMINATED: `try: ... finally: return x` swallows
            # the pending exception, because the return leaves the function
            # before anything re-raises. Propagating anyway would emit into a
            # block that already ends in `ret`, which the builder refuses --
            # and it refuses correctly, since the code would be unreachable.
            self._dyn_finally(node)
            if self.b.current.terminator is None:
                self._dyn_check_forced()
            if self.b.current.terminator is None:
                self.b.jump(done)

        if rethrow is not None:
            # AFTER the for/else, not between them: an `if` in that gap takes
            # the loop's `else` for its own, which silently turns "no handler
            # matched" into "this statement has no finally".
            resume = self.b.current
            self.b.switch_to(rethrow)
            self._in_finally += 1
            self._dyn_stmts(node.finalbody)
            self._in_finally -= 1
            if self.b.current.terminator is None:
                self._dyn_check_forced()
            # `_dyn_check_forced` LEAVES NO TERMINATOR in the entry function
            # with nothing enclosing it: there it emits `apy_fatal_if_error`,
            # which stops the process when the flag is set and RETURNS when it
            # is not. Inline that is fine -- the caller keeps emitting into
            # the same block -- but this block ends here, so the
            # no-error path needs somewhere to go.
            if self.b.current.terminator is None:
                self.b.jump(done)
            self.b.switch_to(resume)

        self.b.switch_to(done)

    def _dyn_handler_type(self, node) -> int:
        """The type value an `except*` clause matches against.

        `except*` cannot ask `apy_error_matches`, which tests the flag against
        one name: the group has to be DIVIDED, and dividing needs a class to
        hand `isinstance` for each leaf. `except* (A, B)` is one tuple, which
        `isinstance` already accepts.
        """
        names = _handler_names(node)
        if len(names) == 1 and not isinstance(node, ast.Tuple):
            return self._dyn_exc_type(names[0])
        out = self.b.call(T.PTR, "apy_tuple_new",
                          [self.b.const(T.I64, max(1, len(names)))])
        for name in names:
            self.b.call(T.PTR, "apy_seq_push",
                        [out, self._dyn_exc_type(name)])
        return out

    def _dyn_exc_type(self, name: str) -> int:
        out = self.b.call(T.PTR, "apy_exc_type",
                          [self._dyn_str_literal(name)])
        self._dyn_check()
        return out

    def _dyn_at(self, value: int, index: int) -> int:
        out = self.b.call(T.PTR, "apy_getitem",
                          [value, self.b.call(T.PTR, "apy_from_int",
                                              [self.b.const(T.I64, index)])])
        self._dyn_check()
        return out

    def _dyn_is_none(self, value: int) -> int:
        return self._dyn_truth_of(
            self.b.call(T.PTR, "apy_is",
                        [value, self.b.call(T.PTR, "apy_none", [])]))

    def _dyn_try_star(self, node) -> None:
        """`try` / `except*` -- PEP 654's dispatch, which is not `except`'s.

        EVERY clause runs, each holding the part of the group its own type
        matches, and whatever no clause claimed propagates. `except` asks
        "which handler" and stops at the first yes; this asks "how does this
        group divide", which is a different question and needs the whole group
        in one place to answer -- so the dividing happens in
        `apy_group_dispatch` and the lowering reads the answer off a tuple,
        one entry per clause and the leftover last.

        WHAT THIS DOES NOT DO is collect exceptions raised BY the clauses into
        the group that propagates. One raised in a clause body leaves this
        statement on its own, exactly as it would from an ordinary `except`.
        """
        dispatch = self.b.new_block("exceptstar")
        done = self.b.new_block("tryend")
        self.finallys.append(node.finalbody)
        self.handlers.append(dispatch.label)
        self._dyn_stmts(node.body)
        self.handlers.pop()
        self.finallys.pop()
        if self.b.current.terminator is None:
            if node.orelse:
                # Not protected by these clauses, for the reason `try`'s
                # `else` is not: that is what it is for.
                self.finallys.append(node.finalbody)
                self._dyn_stmts(node.orelse)
                self.finallys.pop()
            if self.b.current.terminator is None:
                self._dyn_finally(node)
                if self.b.current.terminator is None:
                    self.b.jump(done)

        self.b.switch_to(dispatch)
        raised = self.b.call(T.PTR, "apy_error_value", [])
        # CLEARED BEFORE THE SPLIT, because splitting calls `isinstance` on
        # every leaf and a set flag would make the first of those calls look
        # like it had failed.
        self.b.call(T.VOID, "apy_error_clear", [])
        types = self.b.call(T.PTR, "apy_tuple_new",
                            [self.b.const(T.I64, max(1, len(node.handlers)))])
        for handler in node.handlers:
            self.b.call(T.PTR, "apy_seq_push",
                        [types, self._dyn_handler_type(handler.type)])
        parts = self._keep(self.b.call(T.PTR, "apy_group_dispatch",
                                       [raised, types]))
        self._dyn_check()

        rethrow = None
        for index, handler in enumerate(node.handlers):
            body_b = self.b.new_block("starhandler")
            after = self.b.new_block("starnext")
            hit = self._keep(self._dyn_at(parts(), index))
            # None means this clause caught nothing, so its body is SKIPPED --
            # and skipping is not stopping: the clauses after it still have
            # their own halves to handle.
            self.b.branch(self._dyn_is_none(hit()), after, body_b)
            self.b.switch_to(body_b)
            self.finallys.append(node.finalbody)
            if handler.name:
                self._dyn_store(handler.name, hit())
            was = self._keep(self.b.call(T.PTR, "apy_error_handling", [hit()]))
            self._handling.append(hit)
            if node.finalbody:
                if rethrow is None:
                    rethrow = self.b.new_block("tryfinraise")
                self.handlers.append(rethrow.label)
            self._dyn_stmts(handler.body)
            if handler.name and self.b.current.terminator is None:
                self._dyn_unbind(handler.name)
            if node.finalbody:
                self.handlers.pop()
            self._handling.pop()
            self.finallys.pop()
            if self.b.current.terminator is None:
                self.b.call(T.PTR, "apy_error_handling", [was()])
                self.b.jump(after)
            self.b.switch_to(after)

        # WHAT NO CLAUSE CLAIMED, which the dispatch left last. It is the
        # original exception untouched when nothing matched at all, so a
        # `try`/`except* KeyError` around a ValueError propagates the
        # ValueError rather than a group wrapping it.
        left = self._dyn_at(parts(), len(node.handlers))
        cleared = self.b.new_block("starclear")
        again = self.b.new_block("starraise")
        self.b.branch(self._dyn_is_none(left), cleared, again)

        self.b.switch_to(again)
        self.b.call(T.PTR, "apy_raise", [left])
        self._dyn_finally(node)
        if self.b.current.terminator is None:
            self._dyn_check_forced()
        if self.b.current.terminator is None:
            self.b.jump(done)

        self.b.switch_to(cleared)
        self._dyn_finally(node)
        if self.b.current.terminator is None:
            self.b.jump(done)

        if rethrow is not None:
            resume = self.b.current
            self.b.switch_to(rethrow)
            self._in_finally += 1
            self._dyn_stmts(node.finalbody)
            self._in_finally -= 1
            if self.b.current.terminator is None:
                self._dyn_check_forced()
            if self.b.current.terminator is None:
                self.b.jump(done)
            self.b.switch_to(resume)

        self.b.switch_to(done)

    #: `+=` and friends -> the runtime entry point that mutates in place.
    #: Only the operators with an in-place form; everything else is the binary
    #: operator, because that IS what `x //= y` means.
    _INPLACE = {ast.Add: None, ast.BitOr: "|", ast.BitAnd: "&",
                ast.BitXor: "^", ast.Sub: "-", ast.Mult: "*"}

    def _dyn_inplace(self, node, current: int) -> int:
        """`x op= y`, evaluated against the value already read.

        The in-place operators MUTATE where the kind has an in-place form: a
        list `+=` extends itself and a set `|=` grows, so every other name
        bound to the object sees it. `apy_iadd` and `apy_iop` decide by kind at
        run time, because the frontend does not know one.
        """
        # THE CURRENT VALUE WAS READ FIRST and the operand may suspend.
        held = self._spill_across_await(current, node.value)
        value = self._dyn_expr(node.value)
        current = held()
        kind = type(node.op)
        if kind is ast.Add:
            out = self.b.call(T.PTR, "apy_iadd", [current, value])
        elif kind in self._INPLACE:
            out = self.b.call(T.PTR, "apy_iop",
                              [current, value,
                               self._dyn_str_literal(self._INPLACE[kind])])
        else:
            out = self.b.call(T.PTR, DYN_BINOP[kind], [current, value])
        self._dyn_check()
        return out

    def _dyn_leave_loop(self, target: str) -> None:
        """`break` or `continue`, running every `finally` it jumps out of.

        The same obligation `return` has and for the same reason -- a
        `finally` runs on EVERY exit from its `try`, and a `break` out of the
        body is one. Only the ones opened INSIDE the loop, which is what the
        depth recorded with the loop is for: a `finally` around the whole loop
        is not being left.

        A `finally` that itself terminates -- returns, or raises -- wins, and
        the jump below is never emitted, which is what makes
        `for ...: try: break finally: return x` answer x.
        """
        pending = self.finallys
        depth = self.loops[-1][2]
        try:
            for i in range(len(pending) - 1, depth - 1, -1):
                self.finallys = pending[:i]
                self._dyn_unwind(pending[i])
                if self.b.current.terminator is not None:
                    break
        finally:
            self.finallys = pending
        if self.b.current.terminator is None:
            self.b.jump(target)

    def _dyn_unwind(self, pending) -> None:
        """Emit one entry of `self.finallys` on a `return`'s way out.

        Two kinds live on that stack. A `try`'s entry is its `finalbody`, a
        list of statements. A `with`'s is a CALLABLE that emits the
        `__exit__(None, None, None)` call -- there is no AST for it, and
        synthesising one would mean re-evaluating the manager expression,
        which Python evaluates exactly once.
        """
        if callable(pending):
            pending()
        else:
            self._in_finally += 1
            self._dyn_stmts(pending)
            self._in_finally -= 1

    #: How many `finally` bodies are being lowered right now. A `return`
    #: inside one discards the exception it was cleaning up after -- see the
    #: `ast.Return` case.
    _in_finally: int = 0

    def _dyn_finally(self, node: ast.Try) -> None:
        if not node.finalbody:
            return
        self._in_finally += 1
        self._dyn_stmts(node.finalbody)
        self._in_finally -= 1

    # ── closures: cells, function values, classes ───────────────────────────
    def _dyn_open_cells(self) -> None:
        """Make this frame's boxes, and unpack the ones it was handed.

        Runs before the body, so every read and write below finds storage that
        already exists. A CELL local starts empty -- the box is what an inner
        function captures, and the value arrives when the assignment does; the
        cell holding None until then is not the same as being assigned None,
        but nothing can observe the difference because analysis has already
        refused a read before assignment.

        A parameter that is captured is BOXED HERE, replacing its register
        with the cell. `def outer(n)` whose inner function reads `n` must have
        the parameter itself in the box, or the closure would capture an empty
        one and the argument would be invisible.
        """
        info = self.info
        for i, name in enumerate(info.freevars):
            sym = info.locals.get(name)
            if sym is None:
                continue
            sym.register = self.b.call(T.PTR, "apy_env_cell",
                                       [self.env, self.b.const(T.I64, i)])
        for name in info.cellvars:
            sym = info.locals.get(name)
            if sym is None:
                continue
            initial = (sym.register if sym.is_param
                       else self.b.call(T.PTR, "apy_none", []))
            cell = self.b.call(T.PTR, "apy_cell_new", [initial])
            if sym.is_param:
                # The parameter's register now names the BOX, not the value.
                # A fresh register would leave the old one live and every read
                # would take whichever the code happened to reach.
                box = self.b.reg(T.PTR)
                self.b.emit(Instruction(Op.COPY, T.PTR, dst=box, args=[cell]))
                sym.register = box
            else:
                self.b.emit(Instruction(Op.COPY, T.PTR, dst=sym.register,
                                        args=[cell]))

    def _dyn_function_value(self, key: str, name_text: str,
                            with_defaults: bool = True) -> int:
        """Build the callable a `def` binds, capturing what it closes over.

        The cells are installed AFTER the object exists, one call each, for
        two reasons: the IR has no varargs, and a recursive inner `def` -- one
        whose own name is among the boxes it captures -- needs the value to
        exist before the box naming it can hold it.
        """
        info = self.infos[key]
        code = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.FUNC_ADDR, T.PTR, dst=code,
                                sym=self.symbols[key]))
        arity = (len(info.params) + (1 if info.vararg else 0)
                 + (1 if info.kwarg else 0))
        func = self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, arity),
                            self._dyn_str_literal(name_text),
                            self.b.const(T.I64, len(info.freevars)),
                            self.b.const(T.I64, len(info.defaults)),
                            self.b.const(T.I64, 1 if info.vararg else 0)])
        if key != name_text:
            # PEP 3155: the QUALIFIED name. The frontend's key is already in
            # exactly CPython's spelling -- `C.m`, `outer.<locals>.inner` --
            # so a name that differs from the plain one IS the qualname.
            self.b.call(T.PTR, "apy_func_qualname",
                        [func, self._dyn_str_literal(key)])
        # PEP 649: the thunk that BUILDS `__annotations__`, recorded on the
        # function so that reading them is what evaluates them.
        annotate = self._dyn_annotate_thunk(key, info)
        if annotate is not None:
            self.b.call(T.PTR, "apy_func_annotate", [func, annotate])
        if info.is_coroutine:
            # Recorded on the FUNCTION, not only on what calling it builds:
            # `inspect.iscoroutinefunction(f)` asks before any call.
            self.b.call(T.PTR, "apy_func_coro", [func])
        # The parameter NAMES, for a keyword argument passed through a value.
        # A direct call matches them at compile time and never reads these; a
        # call through `apy_call` reaches a function whose `def` the caller
        # never saw, and without them `C(1, swallow=True)` had nowhere to look
        # and quietly took the default.
        for i, param in enumerate(info.params):
            self.b.call(T.PTR, "apy_func_param",
                        [func, self.b.const(T.I64, i),
                         self._dyn_str_literal(param.name)])
        # `*rest` AND `**kw` HAVE NAMES TOO, after every declared parameter --
        # which is where CPython puts them in `co_varnames`, and where a
        # signature rebuilt from one expects to find them. Recorded here
        # rather than in `info.params` because they are not parameters the
        # arity checks count.
        extra = [one for one in (info.vararg, info.kwarg) if one]
        for offset, one in enumerate(extra):
            self.b.call(T.PTR, "apy_func_param",
                        [func, self.b.const(T.I64, len(info.params) + offset),
                         self._dyn_str_literal(one)])
        # A positional-only parameter's NAME IS RECORDED and simply does not
        # match -- so a call that passes one by keyword can be told which
        # mistake it made rather than being sent looking for a typo.
        # THE DOCSTRING, if the body opens with one. Dropped as a statement
        # by lowering -- a bare string expression does nothing -- so this is
        # the only place it survives, and `f.__doc__` is the one piece of a
        # `def` a program routinely reads back.
        body = getattr(info.node, "body", ())
        if body and isinstance(body[0], ast.Expr)                 and isinstance(body[0].value, ast.Constant)                 and isinstance(body[0].value.value, str):
            self.b.call(T.PTR, "apy_func_doc",
                        [func, self._dyn_str_literal(body[0].value.value)])
        if info.posonly:
            self.b.call(T.PTR, "apy_func_posonly",
                        [func, self.b.const(T.I64, info.posonly)])
        if info.kwonly:
            self.b.call(T.PTR, "apy_func_kwonly",
                        [func, self.b.const(T.I64, info.kwonly)])
        # HOW MANY OF THE TRAILING DEFAULTS ARE THE KEYWORD-ONLY ONES'. Not
        # `kwonly`: one of those may be REQUIRED, and `def f(a, b=1, *args,
        # c)` has one keyword-only parameter and one default that is not its.
        if info.kwdefaults:
            self.b.call(T.PTR, "apy_func_kwdefaults",
                        [func, self.b.const(T.I64, info.kwdefaults)])
        if info.kwarg:
            self.b.call(T.PTR, "apy_func_kwarg",
                        [func, self.b.const(T.I64, 1)])
        for i, expr in enumerate(info.defaults):
            # EVALUATED HERE, where the `def` statement runs, which is what
            # CPython does and what makes both halves right at once:
            #
            #   `def f(xs=[])` at module level runs once, so one list is
            #   shared by every call that omits the argument;
            #   `def g(n=i)` inside a loop runs once per iteration, so each
            #   function gets that iteration's value.
            #
            # Evaluating them all at program start got the first right and the
            # second wrong -- and wrong by NameError, because `i` had not been
            # bound yet when the entry started.
            if not with_defaults:
                # THE PRE-BINDING AT PROGRAM START asks for none: a default
                # expression may name something the module body has not bound
                # yet, and evaluating it there was a NameError for a program
                # CPython runs. The `def` statement fills them in where it is
                # written, which is where Python evaluates them.
                continue
            value = self._dyn_expr(expr)
            self.b.call(T.PTR, "apy_func_default",
                        [func, self.b.const(T.I64, i), value])
            # The same value into the cell the DIRECT call path reads. Two
            # readers, one evaluation: a direct call and a call through the
            # value must not see different defaults.
            addr = self.b.reg(T.PTR)
            self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr,
                                    sym=self.default_symbol(info, i)))
            self.b.store(T.PTR, value, addr)
        for i, free in enumerate(info.freevars):
            # The box comes from THIS frame: either a local it owns or one it
            # was itself handed. Both are already in a register holding the
            # cell, which is what makes a two-level capture work without the
            # middle function mentioning the name.
            sym = self.info.locals.get(free)
            if sym is None or sym.register is None:
                continue
            self.b.call(T.PTR, "apy_func_cell",
                        [func, self.b.const(T.I64, i), sym.register])
        return func

    def _dyn_module(self, name: str) -> int:
        """`import math` -- the namespace, built where the statement is.

        A TYPE VALUE holding the members, because that is what this runtime
        already has that answers an attribute lookup with an UNBOUND result:
        `math.sqrt` must be the function itself, and an instance would hand
        back a bound method. `type(math).__name__` says `type` rather than
        `module`, which is the one thing a program can see.

        ONE NAMESPACE PER MODULE PER PROGRAM, so `import math as m` twice gives
        `math is m` -- which is what a module being a singleton means, and what
        the case checks.
        """
        # KEYED BY THE TABLE, not by the name. `import cinfo` and `import
        # c.cinfo` are two spellings of one module, and `cinfo is ci` has to
        # be True -- caching by name built two namespaces and answered False.
        table = resolve(name)
        key = id(table)
        got = self._modules.get(key)
        if got is not None:
            return got
        ns = self.b.call(T.PTR, "apy_type_new",
                         [self._dyn_str_literal(name),
                          self.b.call(T.PTR, "apy_none", [])])
        self._dyn_check()
        for attr in table:
            # A backend's own TYPE has no value in the object runtime: it is
            # not an int, a callable or a namespace, it is a Java class, and
            # the only thing that can use one is a statically typed function
            # calling into it. Skipping it leaves the module object without
            # that attribute, which is exactly true -- dynamic code cannot
            # reach it.
            found = member(name, attr)
            if found is not None and found[0] == "jclass":
                continue
            self.b.call(T.PTR, "apy_type_set",
                        [ns, self._dyn_str_literal(attr),
                         self._dyn_member(name, attr)])
            self._dyn_check()
        self._modules[key] = ns
        return ns

    def _dyn_package(self, dotted: str) -> int:
        """The HEAD of `import a.b`, holding `b`.

        `import c.math` binds `c`, and `c.math` is an attribute of it -- which
        is Python's rule and the reason `c.math.sqrt` reads two attributes off
        one name rather than looking up a name with a dot in it. One package
        per head per program, so `import c.math` and `import c.cinfo` fill the
        same `c`.
        """
        head, rest = dotted.split(".", 1)
        pkg = self._modules.get("//" + head)
        if pkg is None:
            pkg = self.b.call(T.PTR, "apy_type_new",
                              [self._dyn_str_literal(head),
                               self.b.call(T.PTR, "apy_none", [])])
            self._dyn_check()
            self._modules["//" + head] = pkg
        self.b.call(T.PTR, "apy_type_set",
                    [pkg, self._dyn_str_literal(rest),
                     self._dyn_module(dotted)])
        self._dyn_check()
        return pkg

    def _dyn_type_name(self, node) -> int:
        """What `isinstance`'s second argument travels as.

        A CLASS VALUE wherever there is one -- a user class, a dotted
        `ast.Name`, a variable holding a type -- so that two classes of the
        same name are not confused with each other.

        A BARE BUILTIN NAME is the exception, and travels as TEXT. There is no
        `int` value to compare against: the builtin types are kinds rather
        than objects, and the name is also what knows the exception hierarchy,
        so `isinstance(e, LookupError)` catching a KeyError is a fact about
        the table and not about a base pointer.
        """
        if isinstance(node, ast.Name):
            # A USER CLASS travels as the class itself, so two classes of the
            # same name are not confused with each other.
            if node.id in self.class_names:
                return self._dyn_load(node.id)
            # AN EXCEPTION NAME travels as TEXT even though it has a value
            # form -- including a user-defined one. The hierarchy is a table
            # of names, because `raise` and `except` match on the name and
            # never hold a class, so a class comparison would answer False for
            # `isinstance(e, AppError)` on a SubError.
            if node.id in _EXC_NAMES or node.id in self.exc_classes:
                return self._dyn_str_literal(node.id)
            # A LOCAL or a module name is a value: `isinstance(x, kinds)`.
            if self.info.locals.get(node.id) is not None                     or self._is_module_name(node.id):
                return self._dyn_expr(node)
            # A BUILTIN KIND. There is no `int` value to compare against.
            return self._dyn_str_literal(node.id)
        return self._dyn_expr(node)

    def _dyn_member_payload(self, attr: str, entry) -> int:
        """One member from its table entry, without looking the module up.

        Split out so a nested namespace can build its own members with the
        same rules the top level uses -- otherwise `sys.implementation` would
        need a second, drifting copy of them.
        """
        kind, *payload = entry
        if kind == "exc":
            # AN EXCEPTION NAME AS A VALUE, interned by name so that
            # `asyncio.TimeoutError is TimeoutError` -- which is a fact about
            # the hierarchy, since they are one name.
            out = self.b.call(T.PTR, "apy_exc_type",
                              [self._dyn_str_literal(payload[0])])
            self._dyn_check()
            return out
        if kind == "str":
            return self._dyn_str_literal(payload[0])
        if kind == "float":
            return self.b.call(T.PTR, "apy_from_float",
                               [self.b.const(T.F64, payload[0])])
        if kind == "int":
            return self.b.call(T.PTR, "apy_from_int",
                               [self.b.const(T.I64, payload[0])])
        if kind == "tuple":
            out = self.b.call(T.PTR, "apy_tuple_new",
                              [self.b.const(T.I64, max(1, len(payload[0])))])
            for n in payload[0]:
                self.b.call(T.PTR, "apy_seq_push",
                            [out, self.b.call(T.PTR, "apy_from_int",
                                              [self.b.const(T.I64, n)])])
            return out
        raise AssertionError(f"unhandled module member kind {kind!r}")

    def _dyn_member(self, module: str, attr: str) -> int:
        """One member of a builtin module, as a value.

        A constant is a literal; a function becomes a callable of the right
        arity, so `math.sqrt(2)` and `sorted(xs, key=math.sqrt)` are the same
        object reached two ways.
        """
        kind, *payload = member(module, attr)
        if kind == "exc":
            # AN EXCEPTION NAME AS A VALUE, interned by name -- so
            # `asyncio.TimeoutError is TimeoutError` answers True, which it
            # must, since the two spellings are one name in the hierarchy.
            out = self.b.call(T.PTR, "apy_exc_type",
                              [self._dyn_str_literal(payload[0])])
            self._dyn_check()
            return out
        if kind == "str":
            return self._dyn_str_literal(payload[0])
        if kind == "float":
            return self.b.call(T.PTR, "apy_from_float",
                               [self.b.const(T.F64, payload[0])])
        if kind == "int":
            return self.b.call(T.PTR, "apy_from_int",
                               [self.b.const(T.I64, payload[0])])
        if kind == "ns":
            # A NESTED NAMESPACE -- `sys.implementation`. Built as a type
            # object with attributes, which is the same shape a module itself
            # is, so nothing new is needed to reach `sys.implementation.name`.
            inner = self.b.call(T.PTR, "apy_type_new",
                                [self._dyn_str_literal(attr),
                                 self.b.call(T.PTR, "apy_none", [])])
            self._dyn_check()
            for sub_name, sub_payload in payload[0].items():
                self.b.call(T.PTR, "apy_type_set",
                            [inner, self._dyn_str_literal(sub_name),
                             self._dyn_member_payload(sub_name, sub_payload)])
                self._dyn_check()
            return inner
        if kind == "form":
            # A `typing` special form. A VALUE and not a callable: a program
            # names it in an annotation and may print its class, and that is
            # the whole of what it does.
            return self.b.call(T.PTR, "apy_typing_form",
                               [self._dyn_str_literal(payload[0])])
        if kind == "intrinsic":
            # AN INSTRUCTION, NOT A FUNCTION. `("intrinsic", symbol, params,
            # ret)` names something a backend splices inline -- `outb` is two
            # moves and an `out`, not a call to anything. It differs from
            # `call` in taking MACHINE words rather than boxed values, so the
            # wrapper below unboxes each argument to the width the
            # instruction reads and boxes the answer back, which is the same
            # conversion a `ctypes` call already gets.
            return self._dyn_intrinsic_value(attr, payload[0], payload[1],
                                             payload[2])
        if kind == "callv":
            # VARIADIC: one parameter that collects every argument into a
            # tuple, which is how `asyncio.gather(a, b, c)` reaches a runtime
            # entry point that cannot have a fixed arity.
            return self._dyn_native_value(payload[0], 1, attr, vararg=True)
        return self._dyn_native_value(payload[0], payload[1], attr,
                                      *payload[2:])

    def _dyn_native_value(self, symbol: str, arity: int, name: str,
                          params=(), defaults=(), vararg: bool = False) -> int:
        """A runtime function as a CALLABLE VALUE, via a synthesised wrapper.

        The same shape `_dyn_builtin_value` builds for `len` and `repr`, with
        an arity that is not always one -- `math.gcd` takes two. One wrapper
        per symbol per module, emitted on first use, so `math.sqrt` written
        twice is one function and two references to it.

        `vararg` makes the single parameter collect everything into a tuple,
        the shape a variadic runtime entry point takes.
        """
        wrapper = self._native_thunks.get(symbol)
        if wrapper is None:
            wrapper = f"pyn_{symbol}"
            self._native_thunks[symbol] = wrapper
            self._pending_natives.append((symbol, wrapper, arity))
        code = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.FUNC_ADDR, T.PTR, dst=code, sym=wrapper))
        func = self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, arity),
                            self._dyn_str_literal(name),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, len(defaults)),
                            self.b.const(T.I64, 1 if vararg else 0)])
        # The parameter NAMES, so a keyword argument can find its slot --
        # `math.isclose(a, b, rel_tol=0.1)` is how the tolerances are always
        # written, and without them the call reports an unexpected keyword.
        for i, param in enumerate(params):
            self.b.call(T.PTR, "apy_func_param",
                        [func, self.b.const(T.I64, i),
                         self._dyn_str_literal(param)])
        for i, value in enumerate(defaults):
            self.b.call(T.PTR, "apy_func_default",
                        [func, self.b.const(T.I64, i),
                         self.b.call(T.PTR, "apy_from_float",
                                     [self.b.const(T.F64, value)])])
        return func

    def _dyn_intrinsic_value(self, name: str, symbol: str, params, ret) -> int:
        """An intrinsic as a CALLABLE VALUE, via a wrapper that converts.

        A WRAPPER AND NOT A CALL SITE, deliberately. An imported member is
        bound as a value -- `from <backend>.<mod> import outb` stores `outb`
        like any other name -- so by the time a call is lowered there is no
        name left to recognise, only a callable. Giving the intrinsic a wrapper
        makes `outb(0x3F8, 65)` and `map(outb, ports)` the same object reached
        two ways, which is what every other module member already is.

        The instruction is still emitted INLINE -- inside the wrapper, where
        the backend splices it. What the wrapper costs is one call, not a
        call to something that does not exist.
        """
        wrapper = self._native_thunks.get(symbol)
        if wrapper is None:
            wrapper = f"pyn_{symbol}"
            self._native_thunks[symbol] = wrapper
            self._pending_natives.append(
                (symbol, wrapper, len(params), (params, ret)))
        code = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.FUNC_ADDR, T.PTR, dst=code, sym=wrapper))
        func = self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, len(params)),
                            self._dyn_str_literal(name),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0)])
        return func

    def _dyn_emit_natives(self) -> None:
        """Emit the body of every native wrapper this module asked for.

        Drained after the real functions, like the builtin thunks and for the
        same reason: asking for one is what creates it, and that happens while
        lowering something else.
        """
        i = 0
        while i < len(self._pending_natives):
            pending = self._pending_natives[i]
            symbol, wrapper, arity = pending[0], pending[1], pending[2]
            # A fourth element means an INTRINSIC: machine types either side
            # rather than boxed values all the way through.
            signature = pending[3] if len(pending) > 3 else None
            i += 1
            fn = Function(wrapper, T.PTR, linkage=Linkage.INTERNAL)
            # The ENV first, as every callable takes -- see `apy_func_new`.
            fn.params.append(fn.new_register(T.PTR))
            args = []
            for _ in range(arity):
                reg = fn.new_register(T.PTR)
                fn.params.append(reg)
                args.append(reg)
            saved_b, saved_info = self.b, self.info
            self.b = Builder(fn)
            self.b.switch_to(self.b.new_block("entry"))
            if signature is None:
                out = self.b.call(T.PTR, symbol, args)
            else:
                out = self._dyn_intrinsic_body(symbol, args, *signature)
            self.b.ret(out)
            self.module.functions.append(fn)
            self.b, self.info = saved_b, saved_info

    def _dyn_intrinsic_body(self, symbol: str, args, params, ret) -> int:
        """Unbox, execute the instruction, box the answer.

        THE SAME SHAPE `_dyn_ctypes_call` USES, and simpler: an intrinsic
        takes integers and addresses only. There are no float widths to
        choose between and no variadic tail, because an instruction has a
        fixed operand list -- that is what makes it an instruction.

        A `ptr` ARGUMENT IS AN ADDRESS, not a string's bytes. `mmio32_write`
        takes the place to store to, and the value a program computed for it
        is an integer; running it through `apy_str_bytes` would demand a
        buffer where the program correctly passed a number.
        """
        machine = []
        for value, want in zip(args, params):
            raw = self.b.call(T.I64, "apy_as_int", [value])
            self._dyn_check()
            machine.append(self._intrinsic_width(raw, T.I64,
                                                 TO_IR[sem_type(want)]))
        result = self.b.call(TO_IR[sem_type(ret)], symbol, machine)
        wide = self._intrinsic_width(result, TO_IR[sem_type(ret)], T.I64)
        return self.b.call(T.PTR, "apy_from_int", [wide])

    def _intrinsic_width(self, value: int, src: T.Type, dst: T.Type) -> int:
        """One value at the width the instruction reads.

        `_cffi_width` ALMOST DOES THIS, and the difference is `ptr`. There a
        pointer argument is a string's buffer, so the conversion never starts
        from an integer; here it always does -- `mmio32_write(0x90000, v)`
        names an ADDRESS, and the address is a number the program computed.
        An integer and a pointer are the same width, so the change is a
        bitcast; routing it through the width rules instead picks `trunc`,
        which the verifier rejects because a pointer has no width to truncate
        to.
        """
        if src == dst:
            return value
        if dst == T.PTR or src == T.PTR:
            out = self.b.reg(dst)
            self.b.emit(Instruction(Op.BITCAST, dst, dst=out, args=[value]))
            return out
        return self._cffi_width(value, src, dst)

    def _dyn_generator(self, info, fn) -> None:
        """A generator, as a CONSTRUCTOR and a STEP function.

        The constructor is `fn`, the function the `def` binds: it allocates
        the generator, stores the parameters into its frame slots, and returns
        it. None of the body runs, which is why `g()` on a generator whose
        first statement prints does not print.

        The step is a second IR function holding the body, re-entered once per
        `next`. It opens with a DISPATCH on the saved state -- a chain of
        compares, since the IR has no switch -- that jumps to the block after
        whichever `yield` last returned. Each `yield` stores the state, returns
        the value, and names the block to come back to.
        """
        # `_step`, not `$step`: the C backend writes a function name
        # straight into the generated source, and `$` is not a C
        # identifier character. The suffix has to be spellable by
        # every backend, not only by the IR printer.
        step_sym = self.symbols[info.name] + "_step"

        # THE STEP IS LOWERED FIRST. Its body may ask for frame slots of its
        # own -- a `for` inside a generator keeps its index there -- so the
        # slot COUNT is not known until the body is done, and the constructor
        # below needs that count.
        body = Function(step_sym, T.PTR, linkage=Linkage.INTERNAL)
        body.params.append(body.new_register(T.PTR))      # the env
        gen_reg = body.new_register(T.PTR)
        body.params.append(gen_reg)
        saved_b, saved_gen, saved_fn = self.b, self._gen, self.fn
        saved_loops, saved_fin, saved_handlers = (self.loops, self.finallys,
                                                 self.handlers)
        self.b, self.fn = Builder(body), body
        self._gen = (gen_reg, info.slots)
        self.loops, self.finallys, self.handlers = [], [], []
        self.b.switch_to(self.b.new_block("entry"))
        state = self.b.call(T.I64, "apy_gen_state", [gen_reg])
        start = self.b.new_block("genstart")
        # The dispatch is built AFTER the body, when every resume block is
        # known -- so the entry block ends with a chain filled in below.
        self._gen_resumes = []
        dispatch_at = self.b.current
        self.b.switch_to(start)
        self._dyn_stmts(info.node.body)
        if self.b.current.terminator is None:
            self._dyn_gen_finish()
        self.b.switch_to(dispatch_at)
        for k, block in self._gen_resumes:
            hit = self.b.cmp(Op.EQ, T.I64, state, self.b.const(T.I64, k))
            nxt = self.b.new_block("gendispatch")
            self.b.branch(hit, block, nxt)
            self.b.switch_to(nxt)
        self.b.jump(start)
        self.module.functions.append(body)
        self.b, self._gen, self.fn = saved_b, saved_gen, saved_fn
        self.loops, self.finallys, self.handlers = (saved_loops, saved_fin,
                                                    saved_handlers)

        # -- the constructor, now that the frame's size is known -------------
        code = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.FUNC_ADDR, T.PTR, dst=code, sym=step_sym))
        step = self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, 1),
                            self._dyn_str_literal(info.node.name),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0)])
        gen = self.b.call(T.PTR, "apy_gen_new",
                          [step, self.b.const(T.I64, len(info.slots))])
        if info.is_async_generator:
            # `async def` WITH `yield` -- driven by `async for`, not awaited.
            self.b.call(T.PTR, "apy_agen_mark", [gen])
        elif info.is_coroutine:
            # Same object, different name: `type(f()).__name__` is 'coroutine'
            # and that is how a program tells one from a generator.
            self.b.call(T.PTR, "apy_coro_mark", [gen])
        for sym in info.params:
            if sym.name in info.slots and sym.register is not None:
                self.b.call(T.PTR, "apy_gen_set",
                            [gen, self.b.const(T.I64, info.slots[sym.name]),
                             sym.register])
        for extra in (info.vararg, info.kwarg):
            if extra and extra in info.slots:
                got = info.locals[extra]
                if got.register is not None:
                    self.b.call(T.PTR, "apy_gen_set",
                                [gen, self.b.const(T.I64, info.slots[extra]),
                                 got.register])
        self.b.ret(gen)

    def _gen_temp(self) -> int | None:
        """A frame slot for a COMPILER TEMPORARY, or None outside a generator.

        A `for` inside a generator keeps its index and the sequence it walks
        in the frame, because a `yield` in the body returns from the step and
        a register does not survive that. The slot map is a dict of names, so
        these get names no Python identifier can collide with.
        """
        if self._gen is None:
            return None
        slots = self._gen[1]
        key = f"<temp{len(slots)}>"
        slots[key] = len(slots)
        return slots[key]

    def _keep(self, value: int):
        """A value that must survive a `yield`, and how to read it back.

        Inside a generator a register does not cross a suspension: the step
        returns and comes back at a block the register was never written on.
        Anything the compiler holds across a `yield` -- a `with`'s manager, a
        handler's saved context -- therefore goes to a frame slot, and this
        hands back the reader for it. Outside a generator it is the register,
        unchanged and free.
        """
        if self._gen is None:
            return lambda: value
        at = self._gen_temp()
        self._gen_put(at, value)
        return lambda: self._gen_get(at)

    def _gen_put(self, at: int, value: int, raw: bool = False) -> None:
        self.b.call(T.PTR, "apy_gen_iset" if raw else "apy_gen_set",
                    [self._gen[0], self.b.const(T.I64, at), value])

    def _gen_get(self, at: int, raw: bool = False) -> int:
        return self.b.call(T.I64 if raw else T.PTR,
                           "apy_gen_iget" if raw else "apy_gen_slot",
                           [self._gen[0], self.b.const(T.I64, at)])

    def _dyn_gen_finish(self) -> None:
        """Leave the step for the last time: mark the generator done and
        return. The caller reads the STATE to tell "yielded None" from
        "finished", so the value returned here does not matter."""
        gen_reg, _ = self._gen
        self.b.call(T.PTR, "apy_gen_goto",
                    [gen_reg, self.b.const(T.I64, -1)])
        self.b.ret(self.b.call(T.PTR, "apy_none", []))

    def _dyn_yield(self, node) -> int:
        """One `yield`: save where to come back to, return the value, and name
        the block that resumes.

        `yield` is an EXPRESSION -- its value is whatever `send` passed in, or
        None for a plain `next` -- so it answers a register like any other.
        """
        value = (self._dyn_expr(node.value) if node.value is not None
                 else self.b.call(T.PTR, "apy_none", []))
        return self._dyn_yield_value(value)

    def _dyn_yield_from(self, node) -> int:
        """`yield from it` -- every element of `it`, yielded by this generator.

        Lowered as the loop it stands for. The DELEGATION is real: the outer
        generator still suspends once per element, so a consumer stepping it
        sees them one at a time and in order. What is not real is laziness on
        the INNER side -- `apy_iterable` drains a generator, for the reason the
        `for` loop documents -- so an infinite inner generator does not work
        where an infinite outer one does.

        THE VALUE OF THE EXPRESSION is the inner generator's return value --
        `got = yield from inner()` reads what `inner` returned, which is the
        whole reason a `return` in a generator carries anything. It is read
        off the object rather than caught as a StopIteration because the
        delegation drains rather than stepping, so no exception is left by the
        time the loop ends.
        """
        source = self._dyn_expr(node.value)
        at_src = self._gen_temp()
        at_sent = self._gen_temp()
        # A NON-GENERATOR BECOMES A CURSOR here, so one loop steps both: a
        # generator takes the sent value and a cursor has nowhere to put it.
        walk = self.b.call(T.PTR, "apy_getiter", [source])
        self._dyn_check()
        self._gen_put(at_src, walk)
        self._gen_put(at_sent, self.b.call(T.PTR, "apy_none", []))
        test = self.b.new_block("yftest")
        body = self.b.new_block("yfbody")
        done = self.b.new_block("yfend")
        self.b.jump(test)

        self.b.switch_to(test)
        item = self.b.call(T.PTR, "apy_delegate_step",
                           [self._gen_get(at_src), self._gen_get(at_sent)])
        self._dyn_check()
        at_item = self._gen_temp()
        self._gen_put(at_item, item)
        stop = self.b.call(T.I64, "apy_is_stop", [self._gen_get(at_item)])
        self.b.branch(self.b.cmp(Op.NE, T.I64, stop, self.b.const(T.I64, 0)),
                      done, body)

        self.b.switch_to(body)
        # WHAT THE OUTER GENERATOR IS SENT GOES TO THE INNER ONE on the next
        # step, which is the whole of the delegation: `got = yield ...` inside
        # the inner generator reads what the consumer sent to the outer.
        self._gen_put(at_sent,
                      self._dyn_yield_value(self._gen_get(at_item)))
        self.b.jump(test)

        self.b.switch_to(done)
        return self.b.call(T.PTR, "apy_gen_taken", [self._gen_get(at_src)])

    def _held_accumulator(self, value: int, parts):
        """A reader for a container being FILLED, safe across a suspension.

        A display holds its accumulator in a register while it evaluates each
        element. An element containing an `await` returns through the step
        function, and the register is gone -- so the push after it read one no
        path had written. Where an await is present the accumulator moves to a
        frame slot and is re-read for each element; where it is not, nothing
        changes and the register is kept.
        """
        if self._gen is None or not any(_suspends(part) for part in parts):
            return lambda: value
        at = self._gen_temp()
        self._gen_put(at, value)
        return lambda: self._gen_get(at)

    def _spill_raw(self, value: int, later):
        """`_spill_across_await` for a MACHINE WORD rather than a handle.

        The generator's object slots hold handles and are read back as such;
        a slice bound is an `i64` from `apy_index`, and putting one through
        them would read it as an object. `apy_gen_iset`/`apy_gen_iget` are the
        raw pair the frame already has for exactly this.
        """
        rest = ([later] if isinstance(later, ast.AST)
                else list(later or ()))
        if self._gen is None or not any(_suspends(one) for one in rest):
            return lambda: value
        at = self._gen_temp()
        self._gen_put(at, value, raw=True)
        return lambda: self._gen_get(at, raw=True)

    def _holder(self, value: int):
        """A value that needs no spilling, as the reader shape one has."""
        return lambda: value

    def _dyn_operands(self, nodes) -> list:
        """Every expression in `nodes`, left to right, each made to survive
        the suspensions in the ones after it.

        AN ARGUMENT LIST IS WHERE THIS BITES HARDEST. `f(await a(), await
        b())` computes the first into a register, suspends inside the second,
        and comes back at a block no path wrote that register on -- so the
        call reads it and the IR verifier refuses the program, naming a block
        the source never had. It is ordinary async Python and it was a
        compiler bug reported as the program's.
        """
        readers = []
        items = list(nodes)
        for i, one in enumerate(items):
            readers.append(self._spill_across_await(self._dyn_expr(one),
                                                    items[i + 1:]))
        return [r() for r in readers]

    def _spill_across_await(self, value: int, later):
        """A reader for `value`, made to survive a suspension in `later`.

        A REGISTER DOES NOT SURVIVE A SUSPENSION -- that is why a generator's
        locals live in frame slots -- and an intermediate does not either.
        `await a() + await b()` computed the left operand into a register, then
        the right operand suspended, and the resume path read a register no
        path had written: invalid IR, reported against a block the program
        never wrote.

        Only when there IS an await ahead, and only inside a generator: a
        frame slot per operand everywhere else would cost every expression in
        the language for a shape almost none of them have.
        """
        rest = ([later] if isinstance(later, ast.AST)
                else list(later or ()))
        if self._gen is None or not any(_suspends(one) for one in rest):
            return lambda: value
        at = self._gen_temp()
        self._gen_put(at, value)
        # A READER, not the value: the load has to be emitted AFTER whatever
        # suspends, or it is a register that does not survive either.
        return lambda: self._gen_get(at)

    def _dyn_await(self, node) -> int:
        """`await x` -- run `x`, suspending this coroutine wherever it does.

        DELEGATION, NOT DRAINING, and the difference is the whole reason this
        does not reuse `_dyn_yield_from`. That one calls `apy_iterable`, which
        runs the inner generator to the end before yielding anything; here
        each suspension of `x` has to become a suspension of THIS coroutine,
        so that a chain of awaits parks together on one point.

        Draining would give the same answer for every case that awaits
        sequentially -- which is all of them today -- and would have to be
        torn out the moment two coroutines run concurrently. The scoreboard
        cannot tell the two apart; the next feature can.

        The loop is: step the awaited thing; if it finished, the expression's
        value is what it returned; otherwise yield what it yielded and come
        back here when resumed.
        """
        return self._dyn_await_value(self._dyn_expr(node.value))

    def _dyn_await_value(self, awaited: int) -> int:
        """`await` on a value already computed.

        Split from `_dyn_await` so `async with` can await what `__aenter__`
        returned without an AST node to point at.
        """
        # In a FRAME SLOT, not a register: the loop below suspends, and a
        # register does not survive the return that a suspension compiles to.
        at_awaited = self._gen_temp()
        self._gen_put(at_awaited, awaited)
        at_result = self._gen_temp()
        self._gen_put(at_result, self.b.call(T.PTR, "apy_none", []))

        test = self.b.new_block("awtest")
        body = self.b.new_block("awsuspend")
        after = self.b.new_block("awdone")
        self.b.jump(test)

        self.b.switch_to(test)
        stepped = self.b.call(T.PTR, "apy_await_step",
                              [self._gen_get(at_awaited),
                               self.b.call(T.PTR, "apy_none", [])])
        self._dyn_check()
        # `apy_stop()` is the sentinel for "finished", the same one the
        # iteration protocol uses -- see `apy_await_step`.
        finished = self.b.cmp(Op.EQ, T.PTR, stepped,
                              self.b.call(T.PTR, "apy_stop", []))
        at_stepped = self._gen_temp()
        self._gen_put(at_stepped, stepped)
        self.b.branch(finished, after, body)

        self.b.switch_to(body)
        self._dyn_yield_value(self._gen_get(at_stepped))
        self.b.jump(test)

        self.b.switch_to(after)
        self._gen_put(at_result, self.b.call(
            T.PTR, "apy_gen_taken", [self._gen_get(at_awaited)]))
        return self._gen_get(at_result)

    def _dyn_yield_value(self, value: int) -> int:
        """Suspend here, hand `value` back, and name the block that resumes."""
        gen_reg, _ = self._gen
        resume = self.b.new_block("genresume")
        k = len(self._gen_resumes) + 1
        self._gen_resumes.append((k, resume))
        self.b.call(T.PTR, "apy_gen_goto", [gen_reg, self.b.const(T.I64, k)])
        self.b.ret(value)
        self.b.switch_to(resume)
        # `throw` and `close` RAISE HERE, not at the call site -- which is why
        # a `try` around the `yield` inside the body catches them, and is the
        # whole difference between `g.throw(e)` and `raise e`.
        thrown = self.b.call(T.I64, "apy_gen_throwing", [gen_reg])
        raising = self.b.new_block("genthrow")
        carry_on = self.b.new_block("genresumed")
        self.b.branch(self.b.cmp(Op.NE, T.I64, thrown,
                                 self.b.const(T.I64, 0)),
                      raising, carry_on)
        self.b.switch_to(raising)
        self.b.call(T.PTR, "apy_raise",
                    [self.b.call(T.PTR, "apy_gen_pending", [gen_reg])])
        self._dyn_check_forced()
        self.b.switch_to(carry_on)
        return self.b.call(T.PTR, "apy_gen_sent", [gen_reg])

    def _dyn_decorated(self, node, value: int) -> int:
        """Apply `@decorator` lines to what a `def` or `class` just built.

        BOTTOM-UP, which is what the stacking order means: the line nearest
        the `def` sees the raw function and the one furthest away sees the
        result of all the others. Written the other way round, `@a` over `@b`
        would produce `b(a(f))` -- a program that still runs and computes
        something else, which is the worst kind of wrong.

        A decorator is an ordinary call on a value, so nothing here knows or
        cares whether it returns the same function, a wrapper, or something
        that is not callable at all; the last of those is a TypeError at the
        first use, where Python raises it.
        """
        for deco in reversed(node.decorator_list):
            # `@property`, `@classmethod`, `@staticmethod` NAMED BARE. They are
            # builtins with no value form -- there is no `staticmethod` object
            # to fetch and call -- so the wrapping is emitted here, where the
            # name is still visible as a name. Shadowed by a local of the same
            # name, which is why the scope is checked first.
            if (isinstance(deco, ast.Name)
                    and deco.id in _DESCRIPTOR_KINDS
                    and self.info.locals.get(deco.id) is None
                    and not self._is_module_name(deco.id)):
                value = self.b.call(
                    T.PTR, "apy_descr_new",
                    [value, self.b.const(T.I64, _DESCRIPTOR_KINDS[deco.id])])
                self._dyn_check()
                continue
            # `@v.setter` / `@v.getter`. A bound method of a property is not a
            # value this runtime can produce -- there is no callable to fetch
            # off the descriptor -- so the decorator form is recognised here,
            # where the receiver is still an expression. `p.setter` used as a
            # value anywhere else is still unsupported, and says so.
            if (isinstance(deco, ast.Attribute)
                    and deco.attr in ("setter", "getter", "deleter")):
                value = self.b.call(
                    T.PTR, "apy_prop_" + deco.attr,
                    [self._dyn_expr(deco.value), value])
                self._dyn_check()
                continue
            value = self._dyn_indirect(self._dyn_expr(deco), [value])
        return value

    def _dyn_class(self, node) -> None:
        """`class C(B): ...` -- build the type, fill it, bind the name."""
        key = self.class_of_node[id(node)]
        info = self.classes[key]
        if info.is_exception:
            # THE NAME GOES INTO THE HIERARCHY, always. `raise` and `except`
            # match on it -- that is what makes `except LookupError:` catch a
            # KeyError without either being a value -- so this registration is
            # what makes the class catchable at all.
            self.b.call(T.PTR, "apy_exc_register",
                        [self._dyn_str_literal(info.name),
                         self._dyn_str_literal(info.base)])
            self._dyn_check()
            # AND ITS BASE MUST NOT BE ONE EITHER. `class IndentError(
            # LexError): pass` has an empty body and still needs a class: the
            # `__init__` it runs is its BASE'S, and the fast path below binds
            # no class at all -- so nothing was found to call and the instance
            # came back without the attributes `LexError.__init__` sets.
            inherits_a_body = info.base in self.exc_classes
            if not (info.methods or info.attrs or info.body_exprs
                    or info.annotations or inherits_a_body):
                # NOTHING IN THE BODY, so there is nothing to build. The name
                # is still BOUND to a value, because a program reads
                # `MyError.__mro__` and hands the class to `issubclass`, and
                # binding nothing made every such use a NameError for a class
                # the program plainly defines. Interned by name, so this is
                # the same object the `except` clause finds.
                self._dyn_store(node.name,
                                self.b.call(T.PTR, "apy_exc_type",
                                            [self._dyn_str_literal(info.name)]))
                self._dyn_check()
                return
            # A BODY, so the class is built like any other and the rest of
            # this method does it. `apy_exc_class_bind` below is what ties the
            # two halves together: it hands the runtime the class to find from
            # the name, which is all `apy_make_excn` is given.
        derived = info.base is not None or info.is_meta
        base = (self._dyn_load(info.base) if info.base is not None
                else self.b.call(T.PTR, "apy_type_class", [])
                if info.is_meta
                else self.b.call(T.PTR, "apy_none", []))
        # EVERY BASE, in the order written. The first is `base` above -- it is
        # what `__base__` answers and what a single-base walk uses -- and the
        # rest join it here, because the C3 linearisation is computed from the
        # whole list at run time.
        extra = [self._dyn_load(one) for one in info.bases[1:]]
        # EVERY CLASS RUNS ITS BODY INTO A MAPPING, and the class is built
        # from it afterwards. Whether a metaclass is involved cannot be
        # decided here: a class with no `metaclass=` still has one if its BASE
        # does, and that is a run-time property of the base. So the lowering
        # is one shape and `apy_class_build` picks.
        meta = (self._dyn_load(info.metaclass) if info.metaclass
                else self.b.call(T.PTR, "apy_none", []))
        bases = self.b.call(T.PTR, "apy_tuple_new",
                            [self.b.const(T.I64, 1)])
        if derived:
            self.b.call(T.PTR, "apy_seq_push", [bases, base])
        for one in extra:
            self.b.call(T.PTR, "apy_seq_push", [bases, one])
        # PEP 560: A BASE THAT IS NOT A CLASS contributes whatever its
        # `__mro_entries__` answers. The written object is kept too, because
        # `__orig_bases__` is what a program reads to see what was actually
        # said -- the resolved base has lost it.
        written = [self._dyn_expr(one) for one in info.base_exprs]
        for one in written:
            resolved = self.b.call(T.PTR, "apy_mro_entries", [one, bases])
            self._dyn_check()
            self.b.call(T.PTR, "apy_seq_push", [bases, resolved])
        cls = self.b.call(T.PTR, "apy_prepare",
                          [meta, self._dyn_str_literal(info.name), bases])
        self._dyn_check()
        setter = "apy_dict_set"
        self._dyn_check()
        # THE BODY'S OWN NAMESPACE while it runs. A class body is a scope
        # executed top to bottom, and a name it bound is readable further down
        # -- `y = x + 1`, and `@v.setter` reading the property that the `def v`
        # above it produced.
        #
        # IN SOURCE ORDER, attributes and methods together. They used to be
        # two passes, so an attribute could not see a method defined ABOVE it
        # (`g = f` after `def f`) and the class dict came out in pass order
        # rather than definition order -- which PEP 520 makes observable.
        outer_scope = self._class_scope
        self._class_scope = {}
        # The class body's bindings, in SOURCE order: a later `def` of the
        # same name replaces an earlier one, exactly as re-assigning does.
        members = ([(getattr(value, "lineno", 0), "attr", (name, value))
                    for name, value in info.attrs]
                   + [(getattr(self.infos[mkey].node, "lineno", 0),
                       "method", mkey) for mkey in info.methods]
                   # A bare expression binds nothing and still RUNS, in its
                   # own place among the rest.
                   + [(getattr(e, "lineno", 0), "expr", e)
                      for e in info.body_exprs]
                   # AND THE STATEMENTS THAT ARE NOT MEMBERS, in their place
                   # among the rest: a class body runs top to bottom, and an
                   # `if` between two assignments sees the first and is seen
                   # by the second.
                   + [(getattr(st, "lineno", 0), "stmt", st)
                      for st in info.body_stmts])
        members.sort(key=lambda m: m[0])
        # WHERE THE GENERAL STATEMENTS' BINDINGS GO. Held for the whole
        # body rather than for one statement, because a member written after
        # an `if` reads what the `if` bound -- and reads it from the same
        # place the write went.
        outer_binds = self._class_binds
        self._class_binds = ((cls, info.name, info.body_stmt_names)
                             if info.body_stmt_names else None)
        for _, kind, payload in members:
            if kind == "stmt":
                self._dyn_stmts([payload])
                continue
            if kind == "expr":
                self._dyn_expr(payload)
                continue
            if kind == "attr":
                name, value = payload
                built = self._dyn_expr(value)
            else:
                minfo = self.infos[payload]
                name = minfo.node.name
                built = self._dyn_decorated(
                    minfo.node, self._dyn_function_value(payload, name))
            self._class_scope[name] = built
            self.b.call(T.PTR, setter,
                        [cls, self._dyn_str_literal(
                            self._mangled(name, info.name)),
                         built])
            self._dyn_check()
        self._class_scope = outer_scope
        self._class_binds = outer_binds
        # THE CLASS IS BUILT FROM THE MAPPING, through the metaclass if there
        # is one -- written here or inherited from the base.
        if info.class_keywords:
            # THE KEYWORDS TRAVEL WITH THE CLASS. Where they land is a
            # run-time question -- a metaclass takes them as arguments, and
            # without one they are for `__init_subclass__`, announced below --
            # so both are handed the same dict and the runtime picks.
            kwd = self.b.call(T.PTR, "apy_dict_new",
                              [self.b.const(T.I64,
                                            len(info.class_keywords) + 1)])
            for kw in node.keywords:
                if kw.arg and kw.arg != "metaclass":
                    self.b.call(T.PTR, "apy_dict_set",
                                [kwd, self._dyn_str_literal(kw.arg),
                                 self._dyn_expr(kw.value)])
            cls = self.b.call(T.PTR, "apy_class_build_kw",
                              [meta, self._dyn_str_literal(info.name),
                               bases, cls, kwd])
        else:
            cls = self.b.call(T.PTR, "apy_class_build",
                              [meta, self._dyn_str_literal(info.name),
                               bases, cls])
        self._dyn_check()
        if info.is_exception:
            # THE NAME NOW HAS A CLASS BEHIND IT. `raise AppError(404, "x")`
            # reaches the runtime with nothing but the name -- the hierarchy
            # is a table of names, and the `class` statement may be in another
            # function entirely -- so the body has to be findable from it, and
            # this is what makes the `__init__` run.
            self.b.call(T.PTR, "apy_exc_class_bind",
                        [self._dyn_str_literal(info.name), cls])
            self._dyn_check()
        # FROM HERE `cls` IS THE CLASS, not the mapping the body ran into, so
        # what is written after it exists is written into a type.
        setter = "apy_type_set"
        if info.builtin_base:
            # `class D(dict)`. The KIND is recorded on the class, and
            # `apy_instance_new` gives every instance a real one.
            self.b.call(T.PTR, "apy_type_builtin",
                        [cls, self.b.const(T.I64,
                                           _BUILTIN_BASE_KIND[
                                               info.builtin_base])])
        if info.base_exprs:
            # PEP 560: what the `class` statement ACTUALLY SAID, before
            # `__mro_entries__` had its say.
            orig = self.b.call(T.PTR, "apy_tuple_new",
                               [self.b.const(T.I64, len(written) + 1)])
            for one in written:
                self.b.call(T.PTR, "apy_seq_push", [orig, one])
            self.b.call(T.PTR, setter,
                        [cls, self._dyn_str_literal("__orig_bases__"), orig])
            self._dyn_check()
        # AFTER THE BODY IS FILLED: `__init_subclass__` routinely reads what
        # the body bound, and it is announced to the BASE rather than to the
        # class itself.
        # `__slots__` NAMING SOMETHING THE BODY ALSO BOUND is a ValueError at
        # class creation -- the slot and the attribute would share a name and
        # the attribute would win silently.
        self.b.call(T.PTR, "apy_check_slots", [cls])
        self._dyn_check()
        # PEP 649: a class body's ANNOTATED NAMES build `C.__annotations__`
        # the same lazy way a function's parameters do -- an annotation may
        # name something that does not exist yet, and only reading them is the
        # error. Stored as `__annotate__` in the class dict, which the type's
        # attribute lookup calls.
        ann = self._dyn_annotate_of("cls_" + info.name, info.annotations)
        if ann is not None:
            self.b.call(T.PTR, setter,
                        [cls, self._dyn_str_literal("__annotate__"), ann])
            self._dyn_check()
        # PEP 487: every descriptor the body bound is TOLD ITS OWN NAME,
        # once, now that the body is complete. It cannot know it otherwise --
        # the expression that built it had no idea what it was about to be
        # assigned to.
        self.b.call(T.PTR, "apy_set_names", [cls])
        self._dyn_check()
        if info.base is not None:
            # THE CLASS KEYWORDS TRAVEL WITH IT: `class A(Base, tag="a")` is
            # how a program configures the hook.
            hook_kw = self.b.call(T.PTR, "apy_dict_new",
                                  [self.b.const(T.I64,
                                                len(info.class_keywords) + 1)])
            for kw in node.keywords:
                if kw.arg and kw.arg != "metaclass":
                    self.b.call(T.PTR, "apy_dict_set",
                                [hook_kw, self._dyn_str_literal(kw.arg),
                                 self._dyn_expr(kw.value)])
            self.b.call(T.PTR, "apy_init_subclass", [cls, hook_kw])
            self._dyn_check()
        self._dyn_store(node.name, self._dyn_decorated(node, cls))

    def _is_callable_value(self, name: str) -> bool:
        """Whether `name` holds a callable VALUE rather than naming a `def`.

        A local, a module global, or a class. Checked before the builtin table
        so that a program which binds `sorted` to something of its own gets
        its own -- which is what Python does, and what the builtin path would
        silently override.
        """
        if name == self._raw_builtin:
            # Inside the builtin's own thunk. The module may have bound this
            # name to something of its own, and the thunk is the way back to
            # the builtin -- routing it through the binding is the recursion
            # it exists to break.
            return False
        if name in self.class_names:
            return True
        if self.info.locals.get(name) is not None:
            return True
        return self._is_module_name(name)

    @property
    def class_names(self) -> set:
        """Names that are TYPE OBJECTS. An exception class is not one."""
        return {c.name for c in self.classes.values() if not c.is_exception}

    def _dyn_load(self, name: str) -> int:
        """Read a name, through whichever storage analysis gave it."""
        if self._class_binds is not None and name in self._class_binds[2]:
            ns, owner, _ = self._class_binds
            out = self.b.call(T.PTR, "apy_ns_get",
                              [ns, self._dyn_str_literal(
                                  self._mangled(name, owner)),
                               self._dyn_str_literal(name)])
            self._dyn_check()
            return out
        got = self._class_scope.get(name)
        if got is not None:
            # A NAME THE CLASS BODY HAS ALREADY BOUND. A class body is a scope
            # that runs top to bottom, so `y = x + 1` reads the `x` two lines
            # up and `@v.setter` reads the property the previous `def v` made.
            # Neither was reachable before: the body's bindings went straight
            # into the type and nothing read them back.
            return got
        got = self._shadow.get(name)
        if got is not None:
            # SHADOWED by a comprehension. Its target is its own, so the read
            # and the write both go to the stand-in register rather than to
            # the enclosing binding of the same spelling.
            return got
        if self._gen is not None and name in self._gen[1]:
            # A GENERATOR'S LOCAL lives in the object, because a register does
            # not survive the return a `yield` compiles to.
            gen_reg, slots = self._gen
            return self.b.call(T.PTR, "apy_gen_slot",
                               [gen_reg, self.b.const(T.I64, slots[name])])
        if name == "__builtins__" and name not in self.info.locals:
            # The same shape a module namespace is -- a type object carrying
            # attributes -- so `dir()` and `hasattr` reach it through paths
            # that already exist. It holds exactly the builtins that HAVE a
            # value form, which is what makes each attribute a callable rather
            # than a marker: `__builtins__.len([1])` works, and a builtin with
            # no value form is honestly absent instead of present and broken.
            ns = self.b.call(T.PTR, "apy_type_new",
                             [self._dyn_str_literal("builtins"),
                              self.b.call(T.PTR, "apy_none", [])])
            self._dyn_check()
            for key in sorted(_VALUE_BUILTINS):
                self.b.call(T.PTR, "apy_type_set",
                            [ns, self._dyn_str_literal(key),
                             self._dyn_builtin_value(key)])
                self._dyn_check()
            return ns
        if self._is_module_name(name):
            return self._dyn_global_read(name)
        sym = self.info.locals.get(name)
        if sym is None and name == "Ellipsis":
            return self.b.call(T.PTR, "apy_ellipsis", [])
        if sym is None and name == "NotImplemented":
            # A SINGLETON, because `x is NotImplemented` is the test programs
            # write and what it means -- "ask the other operand" -- is a
            # signal rather than a value.
            return self.b.call(T.PTR, "apy_notimplemented", [])
        if sym is None and (name in _EXC_NAMES or name in self.exc_classes):
            # An exception NAME used as a value: `issubclass(KeyError, ...)`,
            # `except (A, B)`. There is no global holding one -- the hierarchy
            # is a table of names, because `raise` and `except` match on the
            # name and never hold a class -- so the type object is made here,
            # interned so that the same name is the same object.
            out = self.b.call(T.PTR, "apy_exc_type",
                              [self._dyn_str_literal(name)])
            self._dyn_check()
            return out
        if sym is None and name in MODULE_DUNDERS:
            # `__name__`. No storage, because the program never assigns it --
            # its value is a constant of the compilation, and a compiled
            # program is the script being run, so `__name__` is `"__main__"`.
            value = MODULE_DUNDERS[name]
            if name == "__file__":
                # The path this was compiled FROM. Not a constant of every
                # compilation, which is why it is filled in here rather than
                # written in the table.
                value = str(self.source.path) if self.source.path else "<stdin>"
            return (self.b.call(T.PTR, "apy_none", []) if value is None
                    else self._dyn_str_literal(value))
        if sym is None:
            # A `def` or `class` at module level, named from a function that
            # does not otherwise bind it.
            return self._dyn_global_read(name)
        if sym.storage in (CELL, FREE):
            return self.b.call(T.PTR, "apy_cell_get", [sym.register])
        if name in self.info.maybe_unbound:
            # NOT PROVED ASSIGNED. The register starts null and null is never
            # a value, so this distinguishes "not assigned yet" from
            # "assigned None" -- which is exactly what CPython's
            # UnboundLocalError distinguishes.
            self.b.call(T.PTR, "apy_check_bound",
                        [sym.register, self._dyn_str_literal(name)])
            self._dyn_check()
        return sym.register

    #: While a builtin's thunk body is being lowered, the name it stands
    #: for -- so the body reaches the builtin rather than the module binding
    #: that shadows it. See `_dyn_emit_thunks`.
    _raw_builtin: str | None = None

    def _is_module_name(self, name: str) -> bool:
        """True when `name` refers to the module's storage rather than a local.

        Two ways in: the entry function, where every name IS a module name, and
        any function that either read it as a global or declared it one.
        """
        if name == self._raw_builtin:
            return False
        if name not in self.module_names:
            return False
        if self.info.name == ENTRY_NAME:
            return True
        return name in self.info.module_reads or name in self.info.module_writes

    def _dyn_global_read(self, name: str) -> int:
        """Read a module-level name, raising NameError if it has none yet.

        The cell is zero-initialised and zero is never a valid runtime value,
        so the check distinguishes "not assigned yet" from "assigned None".
        Without it, a program that reads a global before its assignment runs
        got a null pointer and crashed inside the first operation that touched
        it, which reports as a segfault rather than as the NameError CPython
        raises.
        """
        addr = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr,
                                sym=self.global_symbol(name)))
        value = self.b.load(T.PTR, addr)
        if name in _VALUE_BUILTINS:
            # A NAME THAT SHADOWS A BUILTIN falls back to the builtin when the
            # module binding is gone -- which is the last step of Python's
            # name resolution, local then global then builtins. `len = 5`
            # followed by `del len` leaves `len([1, 2])` working, and without
            # this it raised NameError for a name that is always defined.
            return self.b.call(T.PTR, "apy_name_or",
                               [value, self._dyn_builtin_value(name)])
        bound = self.b.new_block("gbound")
        unbound = self.b.new_block("gunbound")
        self.b.branch(self.b.cmp(Op.NE, T.PTR, value,
                                 self.b.const(T.PTR, 0)), bound, unbound)
        self.b.switch_to(unbound)
        self.b.call(T.PTR, "apy_raise",
                    [self.b.call(T.PTR, "apy_make_exc",
                                 [self._dyn_str_literal("NameError"),
                                  self._dyn_str_literal(
                                      f"name '{name}' is not defined")])])
        self._dyn_check_forced()
        if self.b.current.terminator is None:
            self.b.jump(bound)
        self.b.switch_to(bound)
        return value

    def _dyn_global_raw(self, name: str) -> int:
        """A module cell's contents WITHOUT the bound check.

        Zero when the name has never been assigned, which is the one caller's
        whole reason for wanting it: saving and restoring a comprehension's
        target has to be able to put "unbound" back.
        """
        addr = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr,
                                sym=self.global_symbol(name)))
        return self.b.load(T.PTR, addr)

    def _dyn_global_write(self, name: str, value: int) -> None:
        addr = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr,
                                sym=self.global_symbol(name)))
        self.b.store(T.PTR, value, addr)

    def _dyn_bind(self, name: str, value_node) -> None:
        value = self._dyn_expr(value_node)
        self._dyn_store(name, value)

    def _dyn_unbind(self, name: str) -> None:
        """`del x`, for a plain name.

        A module-level name goes back to the zero its cell started at, so a
        later read is the NameError CPython raises rather than a stale value.
        A LOCAL has no such state -- its register is just a register -- but
        analysis has already dropped it from the definitely-assigned set, so a
        later read is a compile error, which is where CPython's
        UnboundLocalError would have been. Nothing to emit for that case.
        """
        if self._is_module_name(name):
            addr = self.b.reg(T.PTR)
            self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=addr,
                                    sym=self.global_symbol(name)))
            self.b.store(T.PTR, self.b.const(T.PTR, 0), addr)

    def _dyn_store(self, name: str, value: int) -> None:
        if self._class_binds is not None and name in self._class_binds[2]:
            # A CLASS ATTRIBUTE BOUND THROUGH CONTROL FLOW. The namespace is a
            # real mapping at run time, so it is storage two branches can both
            # write and a later read can find -- which a register is not, and
            # which is the whole reason this does not go through one.
            ns, owner, _ = self._class_binds
            self.b.call(T.PTR, "apy_dict_set",
                        [ns, self._dyn_str_literal(self._mangled(name, owner)),
                         value])
            self._dyn_check()
            return
        got = self._shadow.get(name)
        if got is not None:
            # SHADOWED by a comprehension -- see `_dyn_comprehension`.
            self.b.emit(Instruction(Op.COPY, T.PTR, dst=got, args=[value]))
            return
        if self._gen is not None and name in self._gen[1]:
            gen_reg, slots = self._gen
            self.b.call(T.PTR, "apy_gen_set",
                        [gen_reg, self.b.const(T.I64, slots[name]), value])
            return
        if self._is_module_name(name):
            self._dyn_global_write(name, value)
            return
        sym = self.info.locals[name]
        if sym.storage in (CELL, FREE):
            # Into the BOX, not over the register holding it. Writing the
            # register would give this frame a private value and leave every
            # closure looking at the original box -- which is the by-value
            # capture that passes every one-closure test and fails
            # `closure-cell-is-shared`.
            self.b.call(T.PTR, "apy_cell_set", [sym.register, value])
            return
        self.b.emit(Instruction(Op.COPY, T.PTR, dst=sym.register,
                                args=[value]))

    #: Builtin classes that MATCH THEMSELVES when given one positional
    #: sub-pattern: `case str(s)` binds the whole string, not an attribute of
    #: it. Every other class reads `__match_args__` instead. Python names
    #: these explicitly, and getting it wrong turns `case int(n)` into a
    #: lookup for an attribute no int has.
    _SELF_MATCHING = frozenset({
        "bool", "bytearray", "bytes", "dict", "float", "frozenset", "int",
        "list", "set", "str", "tuple"})

    def _dyn_match(self, node) -> None:
        """`match subject: case ...` -- the cases as a chain of tests.

        THE SUBJECT IS EVALUATED ONCE, into a slot, and every pattern reads
        that. `match f():` calls `f` a single time however many cases follow,
        which is what makes a `match` over an expensive expression sane.

        Each case gets a failure block: a pattern compiles to tests that jump
        there the moment one fails, and the next case starts from that block.
        A guard is just another test, run AFTER the pattern's bindings so it
        can read them -- `case n if n > 10` is the whole reason the order is
        that way round.

        BINDINGS ARE NOT UNDONE when a later test fails. Python does not undo
        them either: a partially matched pattern leaves its captures set, and
        nothing may read them because no body ran.
        """
        at_subject = self._match_slot(self._dyn_expr(node.subject))
        done = self.b.new_block("matchend")
        for item in node.cases:
            nxt = self.b.new_block("matchnext")
            body = self.b.new_block("matchbody")
            self._dyn_pattern(item.pattern, at_subject, nxt)
            if item.guard is not None:
                # AFTER the pattern, so the guard reads what it bound.
                self.b.branch(self._dyn_truth(item.guard), body, nxt)
            else:
                self.b.jump(body)
            self.b.switch_to(body)
            self._dyn_stmts(item.body)
            if self.b.current.terminator is None:
                self.b.jump(done)
            self.b.switch_to(nxt)
        # Falling off the last case is not an error: a `match` with nothing
        # matching does nothing, which is why `case _` is optional.
        self.b.jump(done)
        self.b.switch_to(done)

    def _match_slot(self, value: int):
        """Hold a value where the pattern chain can re-read it.

        A frame slot inside a generator and a keeper elsewhere, for the reason
        everything else here does it: a `match` inside a generator may suspend
        in a case body, and a register does not survive that.
        """
        if self._gen is not None:
            at = self._gen_temp()
            self._gen_put(at, value)
            return lambda: self._gen_get(at)
        return self._keep(value)

    def _dyn_pattern(self, pat, subject, fail) -> None:
        """Emit the tests for one pattern; jump to `fail` when one does not
        hold. Leaves the builder in a block where the pattern has matched."""
        match pat:
            case ast.MatchAs(pattern=None, name=None):
                return                                   # `case _`
            case ast.MatchAs(pattern=None, name=name):
                self._dyn_store(name, subject())          # `case n`
                return
            case ast.MatchAs(pattern=inner, name=name):
                self._dyn_pattern(inner, subject, fail)
                if name:
                    self._dyn_store(name, subject())
                return
            case ast.MatchValue(value=value):
                self._match_test(self.b.call(
                    T.PTR, "apy_eq", [subject(), self._dyn_expr(value)]), fail)
                return
            case ast.MatchSingleton(value=value):
                # `case None` / `case True` compares by IDENTITY, not equality
                # -- `case True` must not match 1, which `==` would.
                lit = (self.b.call(T.PTR, "apy_none", []) if value is None
                       else self.b.call(T.PTR, "apy_from_bool",
                                        [self.b.const(T.I64, 1 if value
                                                      else 0)]))
                self._match_when(self.b.cmp(Op.EQ, T.PTR, subject(), lit),
                                 fail)
                return
            case ast.MatchOr(patterns=alts):
                self._dyn_pattern_or(alts, subject, fail)
                return
            case ast.MatchSequence(patterns=subs):
                self._dyn_pattern_sequence(subs, subject, fail)
                return
            case ast.MatchMapping():
                self._dyn_pattern_mapping(pat, subject, fail)
                return
            case ast.MatchClass():
                self._dyn_pattern_class(pat, subject, fail)
                return
        raise AssertionError(f"unhandled pattern {type(pat).__name__}")

    def _dyn_pattern_or(self, alts, subject, fail) -> None:
        """`case a | b | c` -- the first alternative that matches wins.

        Each alternative gets its own failure block, which is where the next
        one starts; the last one's failure is the whole pattern's. Every
        alternative that succeeds jumps to ONE continuation, so the code after
        is emitted once however many alternatives there are.
        """
        joined = self.b.new_block("matchor")
        for i, alt in enumerate(alts):
            last = i == len(alts) - 1
            nxt = fail if last else self.b.new_block("matchoralt")
            self._dyn_pattern(alt, subject, nxt)
            self.b.jump(joined)
            if not last:
                self.b.switch_to(nxt)
        self.b.switch_to(joined)

    def _dyn_pattern_sequence(self, subs, subject, fail) -> None:
        """`case [a, b]`, `case [x, *rest]`.

        A str is deliberately NOT a sequence here -- see `apy_match_seq`. The
        length test differs with a star: without one the length must be exact,
        with one it must be at least the number of fixed elements.
        """
        star = next((i for i, sub in enumerate(subs)
                     if isinstance(sub, ast.MatchStar)), None)
        fixed = len(subs) - (1 if star is not None else 0)
        self._match_raw(self.b.call(T.I64, "apy_match_seq", [subject()]), fail)
        length = self.b.call(T.I64, "apy_raw_len", [subject()])
        self._dyn_check()
        at_len = self._match_slot_raw(length)
        self._match_when(
            self.b.cmp(Op.GE if star is not None else Op.EQ, T.I64,
                       at_len(), self.b.const(T.I64, fixed)), fail)
        for i, sub in enumerate(subs):
            if isinstance(sub, ast.MatchStar):
                if sub.name:
                    # Everything between the fixed heads and the fixed tails,
                    # as a LIST whatever the subject was -- as `a, *rest = xs`
                    # binds a list from a tuple.
                    tail = len(subs) - i - 1
                    self._dyn_store(sub.name, self.b.call(
                        T.PTR, "apy_slice",
                        [subject(), self.b.const(T.I64, i),
                         self.b.sub(T.I64, at_len(),
                                    self.b.const(T.I64, tail)),
                         self.b.const(T.I64, 1), self.b.const(T.I64, 1),
                         self.b.const(T.I64, 1)]))
                    self._dyn_check()
                continue
            # BEFORE the star, index from the front; after it, from the back,
            # because how many elements the star swallowed is not known here.
            if star is not None and i > star:
                idx = self.b.sub(T.I64, at_len(),
                                 self.b.const(T.I64, len(subs) - i))
            else:
                idx = self.b.const(T.I64, i)
            item = self.b.call(T.PTR, "apy_key_at", [subject(), idx])
            self._dyn_check()
            self._dyn_pattern(sub, self._match_slot(item), fail)

    def _dyn_pattern_mapping(self, pat, subject, fail) -> None:
        """`case {"k": v}`, `case {**rest}`.

        A mapping pattern is a SUBSET test: the keys it names must be present
        and match, and any others are ignored -- which is why `case {}`
        matches every dict rather than only the empty one.
        """
        self._match_raw(self.b.call(T.I64, "apy_match_map", [subject()]), fail)
        used = self.b.call(T.PTR, "apy_list_new",
                           [self.b.const(T.I64, max(1, len(pat.keys)))])
        at_used = self._match_slot(used)
        for key, sub in zip(pat.keys, pat.patterns):
            at_key = self._match_slot(self._dyn_expr(key))
            self.b.call(T.PTR, "apy_seq_push", [at_used(), at_key()])
            self._match_test(self.b.call(T.PTR, "apy_contains",
                                         [at_key(), subject()]), fail)
            got = self.b.call(T.PTR, "apy_getitem", [subject(), at_key()])
            self._dyn_check()
            self._dyn_pattern(sub, self._match_slot(got), fail)
        if pat.rest:
            self._dyn_store(pat.rest, self.b.call(
                T.PTR, "apy_match_rest", [subject(), at_used()]))
            self._dyn_check()

    def _dyn_pattern_class(self, pat, subject, fail) -> None:
        """`case Point(0, y)`, `case Point(x=0)`, `case int(n)`.

        POSITIONAL SUB-PATTERNS ARE ATTRIBUTE NAMES, read off the class's
        `__match_args__` -- except for the builtin classes that MATCH
        THEMSELVES, where one positional pattern is matched against the whole
        subject: `case str(s)` binds the string, not an attribute of it.
        """
        builtin = (pat.cls.id if isinstance(pat.cls, ast.Name)
                   and pat.cls.id in self._SELF_MATCHING
                   and self.info.locals.get(pat.cls.id) is None else None)
        if builtin is not None:
            # By NAME: a builtin type has no value form here -- `int` is a
            # callable thunk, not a type object -- and `apy_isinstance` takes
            # the name for exactly this reason.
            check = self.b.call(T.PTR, "apy_isinstance",
                                [subject(), self._dyn_str_literal(builtin)])
        else:
            check = self.b.call(T.PTR, "apy_isinstance",
                                [subject(), self._dyn_expr(pat.cls)])
        self._dyn_check()
        self._match_test(check, fail)

        if builtin is not None and pat.patterns:
            # `case str(s)` / `case list([a, b])`: the one positional pattern
            # is matched against the SUBJECT.
            self._dyn_pattern(pat.patterns[0], subject, fail)
        elif pat.patterns:
            at_names = self._match_slot(self.b.call(
                T.PTR, "apy_match_args", [self._dyn_expr(pat.cls)]))
            # A class with too few `__match_args__` cannot match: CPython
            # raises, and refusing the case is the nearest thing that keeps a
            # `match` total.
            self._match_when(
                self.b.cmp(Op.GE, T.I64,
                           self.b.call(T.I64, "apy_raw_len", [at_names()]),
                           self.b.const(T.I64, len(pat.patterns))), fail)
            for i, sub in enumerate(pat.patterns):
                attr = self.b.call(T.PTR, "apy_key_at",
                                   [at_names(), self.b.const(T.I64, i)])
                self._dyn_check()
                got = self.b.call(T.PTR, "apy_getattr", [subject(), attr])
                self._dyn_check()
                self._dyn_pattern(sub, self._match_slot(got), fail)
        for attr, sub in zip(pat.kwd_attrs, pat.kwd_patterns):
            got = self.b.call(T.PTR, "apy_getattr",
                              [subject(), self._dyn_str_literal(attr)])
            self._dyn_check()
            self._dyn_pattern(sub, self._match_slot(got), fail)

    def _match_slot_raw(self, value: int):
        """A machine word held across the pattern chain -- a length, not an
        object. Kept in a frame slot inside a generator, for the same reason
        `_match_slot` exists."""
        if self._gen is not None:
            at = self._gen_temp()
            self._gen_put(at, value, raw=True)
            return lambda: self._gen_get(at, raw=True)
        keeper = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.COPY, T.I64, dst=keeper, args=[value]))
        return lambda: keeper

    def _match_when(self, cond: int, fail) -> None:
        """Carry on where `cond` (an i1) holds, jump to `fail` where it does
        not. Every test in a pattern has this shape, so it is written once --
        and it SWITCHES to the continuation, which is the part that is easy to
        write a branch without."""
        ok = self.b.new_block("matchok")
        self.b.branch(cond, ok, fail)
        self.b.switch_to(ok)

    def _match_test(self, truthy: int, fail) -> None:
        """The same, for a runtime VALUE rather than an i1."""
        self._match_when(self._dyn_truth_of(truthy), fail)

    def _match_raw(self, wide: int, fail) -> None:
        """The same, for an i64 predicate like `apy_match_seq`."""
        self._match_when(self.b.cmp(Op.NE, T.I64, wide,
                                    self.b.const(T.I64, 0)), fail)

    def _dyn_if(self, node: ast.If) -> None:
        then_b = self.b.new_block("then")
        else_b = self.b.new_block("else") if node.orelse else None
        done = self.b.new_block("endif")
        # `else_b if else_b is not None`, NOT `else_b or done`. A Block
        # defines `__len__`, so an EMPTY one is falsy -- and the else block is
        # always empty at this point, because nothing has been lowered into it
        # yet. `or` therefore sent every false edge straight to the join and
        # the else branch of every `if` on this path was unreachable. It
        # printed nothing rather than crashing, which is why hand-written
        # tests missed it and a generated one found it on its first run.
        self.b.branch(self._dyn_truth(node.test), then_b,
                      else_b if else_b is not None else done)
        self.b.switch_to(then_b)
        self._dyn_stmts(node.body)
        if self.b.current.terminator is None:
            self.b.jump(done)
        if else_b is not None:
            self.b.switch_to(else_b)
            self._dyn_stmts(node.orelse)
            if self.b.current.terminator is None:
                self.b.jump(done)
        self.b.switch_to(done)

    def _dyn_while(self, node: ast.While) -> None:
        """`while`, with the `else` clause if there is one.

        `else` runs when the loop ended by its TEST going false, not by a
        `break` -- so it belongs on the test's false edge, and `break` jumps
        past it. Two exit blocks rather than one is the whole mechanism.
        """
        test = self.b.new_block("whiletest")
        body = self.b.new_block("whilebody")
        # `broke` is where `break` goes; `done` is the normal exit, which the
        # else clause sits on.
        done = self.b.new_block("whileelse" if node.orelse else "whileend")
        broke = self.b.new_block("whilebroke") if node.orelse else done
        self.b.jump(test)
        self.b.switch_to(test)
        self.b.branch(self._dyn_truth(node.test), body, done)
        self.b.switch_to(body)
        self.loops.append((test, broke, len(self.finallys)))
        self._dyn_stmts(node.body)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(test)
        self.b.switch_to(done)
        if node.orelse:
            self._dyn_stmts(node.orelse)
            if self.b.current.terminator is None:
                self.b.jump(broke)
            self.b.switch_to(broke)

    def _dyn_for_range(self, node: ast.For, name: str) -> None:
        """`for i in range(...)`, with the COUNTER kept as a machine word.

        The loop variable is a runtime object like every other name -- the body
        may print it, pass it, store it. The counter is not: making it one
        would allocate a cell per iteration and push the loop's own comparison
        through the object runtime, for a value the compiler already knows is
        an int.
        """
        call = node.iter
        raw = [self.b.call(T.I64, "apy_index", [self._dyn_expr(a)])
               for a in call.args]
        if len(raw) == 1:
            start, stop, step = self.b.const(T.I64, 0), raw[0], 1
        elif len(raw) == 2:
            start, stop, step = raw[0], raw[1], 1
        else:
            # Analysis has already required a literal step: its SIGN decides
            # whether the test is `<` or `>`, and a runtime step would need
            # both tests and a branch on the sign.
            start, stop = raw[0], raw[1]
            step = int_literal(call.args[2])

        counter = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.COPY, T.I64, dst=counter, args=[start]))
        # INSIDE A GENERATOR the counter and the bound live in the frame: a
        # `yield` in the body returns from the step function, and a register
        # does not survive that. The same reason `_dyn_for_sequence` keeps its
        # index there, and the reason this loop needed it too -- `for i in
        # range(3): yield i` is the shape every lazy generator has.
        at_i, at_stop = self._gen_temp(), self._gen_temp()
        if at_i is not None:
            self._gen_put(at_i, counter, raw=True)
            self._gen_put(at_stop, stop, raw=True)

        def read_counter():
            return self._gen_get(at_i, raw=True) if at_i is not None else counter

        def read_stop():
            return self._gen_get(at_stop, raw=True) if at_stop is not None                 else stop

        test = self.b.new_block("fortest")
        body = self.b.new_block("forbody")
        step_b = self.b.new_block("forstep")
        done = self.b.new_block("forend")
        broke = (self.b.new_block("forbroke")
                 if node.orelse else done)
        self.b.jump(test)

        self.b.switch_to(test)
        cond = self.b.cmp(Op.LT if step > 0 else Op.GT, T.I64,
                          read_counter(), read_stop())
        self.b.branch(cond, body, done)

        self.b.switch_to(body)
        self._dyn_store(name,
                        self.b.call(T.PTR, "apy_from_int", [read_counter()]))
        self.loops.append((step_b, broke, len(self.finallys)))
        self._dyn_stmts(node.body)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(step_b)

        self.b.switch_to(step_b)
        nxt = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.ADD, T.I64, dst=nxt,
                                args=[read_counter(),
                                      self.b.const(T.I64, step)]))
        if at_i is not None:
            self._gen_put(at_i, nxt, raw=True)
        else:
            self.b.emit(Instruction(Op.COPY, T.I64, dst=counter, args=[nxt]))
        self.b.jump(test)

        self.b.switch_to(done)
        self._dyn_loop_else(node, broke)

    def _dyn_convert_sequence(self, node, name: str) -> int:
        """`list(x)` / `tuple(x)` -- a copy in the other container's kind.

        Walked by index like every other iteration here, for the same reason:
        there is no iterator protocol yet, so the source must be something
        with a length and a subscript.
        """
        source = self.b.call(T.PTR, "apy_iterable",
                             [self._dyn_expr(node.args[0])])
        self._dyn_check()
        slot = self.b.alloca(8)
        self.b.store(T.PTR, source, slot)
        out = self.b.call(
            T.PTR, "apy_tuple_new" if name == "tuple" else "apy_list_new",
            [self.b.const(T.I64, 4)])
        out_slot = self.b.alloca(8)
        self.b.store(T.PTR, out, out_slot)
        length = self.b.call(T.I64, "apy_raw_len", [source])
        self._dyn_check()
        index = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.COPY, T.I64, dst=index,
                                args=[self.b.const(T.I64, 0)]))
        test = self.b.new_block("convtest")
        body = self.b.new_block("convbody")
        done = self.b.new_block("convend")
        self.b.jump(test)
        self.b.switch_to(test)
        self.b.branch(self.b.cmp(Op.LT, T.I64, index, length), body, done)
        self.b.switch_to(body)
        item = self.b.call(T.PTR, "apy_key_at",
                           [self.b.load(T.PTR, slot), index])
        self._dyn_check()
        self.b.call(T.PTR, "apy_seq_push",
                    [self.b.load(T.PTR, out_slot), item])
        nxt = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.ADD, T.I64, dst=nxt,
                                args=[index, self.b.const(T.I64, 1)]))
        self.b.emit(Instruction(Op.COPY, T.I64, dst=index, args=[nxt]))
        self.b.jump(test)
        self.b.switch_to(done)
        return self.b.load(T.PTR, out_slot)

    # ── methods and slicing ─────────────────────────────────────────────────
    def _dyn_method(self, node: ast.Call) -> int:
        """`obj.method(args)`, dispatched on the NAME at compile time.

        The receiver's kind is not known here -- that is what dynamic means --
        so the symbol is chosen by the method name and the argument count
        alone, and the runtime reports when the receiver was the wrong kind.
        That is also where CPython reports it.
        """
        # MANGLED FIRST, so `self.__helper()` finds `_C__helper` -- a private
        # method call is an attribute lookup like any other.
        attr = self._mangle(node.func.attr)
        if isinstance(node.func.value, ast.Call)                 and isinstance(node.func.value.func, ast.Name)                 and node.func.value.func.id == "super"                 and self.info.locals.get("super") is None:
            # `super().m(...)` GOES THROUGH THE SUPER OBJECT, always. The name
            # table below rewrites a call by its NAME alone -- `x.__repr__()`
            # becomes `apy_repr(x)` -- which for a `super()` receiver asked
            # for the repr OF THE SUPER OBJECT rather than for the base's
            # method. The whole point of `super()` is that the receiver
            # decides, so no name may be intercepted ahead of it.
            return self._dyn_indirect(
                self.b.call(T.PTR, "apy_getattr",
                            [self._dyn_expr(node.func.value),
                             self._dyn_attr_literal(attr)]),
                self._dyn_operands(node.args), node.keywords)
        if self._gen is not None and any(_suspends(a) for a in node.args):
            # A SUSPENSION AMONG THE ARGUMENTS, which the name-keyed dispatch
            # below cannot survive: it holds the receiver in a register while
            # it evaluates them, and a register does not cross a suspension --
            # so `log.append(await f())` read one no path had written and was
            # refused as invalid IR, naming a block the program never wrote.
            #
            # THE ATTRIBUTE IS LOOKED UP FIRST, before any argument runs,
            # because that is Python's order: `obj.m(f())` evaluates `obj.m`
            # and then `f()`. Everything then lives in frame slots until the
            # call, which is the only place a suspension cannot lose it.
            bound = self._spill_across_await(
                self.b.call(T.PTR, "apy_getattr",
                            [self._dyn_expr(node.func.value),
                             self._dyn_attr_literal(attr)]),
                node.args)
            self._dyn_check()
            readers = []
            for i, one in enumerate(node.args):
                # EACH ARGUMENT SURVIVES THE ONES AFTER IT, for the same
                # reason: `f(await a, await b)` computes the first and then
                # suspends in the second.
                readers.append(self._spill_across_await(self._dyn_expr(one),
                                                        node.args[i + 1:]))
            return self._dyn_indirect(bound(), [r() for r in readers],
                                      node.keywords)
        receiver = self._dyn_expr(node.func.value)
        if attr == "format" and not any(isinstance(a, ast.Starred)
                                        for a in node.args):
            # `"{} {k}".format(a, k=v)`. The positional arguments travel as a
            # TUPLE and the keyword ones as a dict, because the format string
            # decides at run time which of them each field wants.
            packed = self.b.call(T.PTR, "apy_tuple_new",
                                 [self.b.const(T.I64, len(node.args) + 1)])
            for a in node.args:
                self.b.call(T.PTR, "apy_seq_push", [packed, self._dyn_expr(a)])
            named = self.b.call(T.PTR, "apy_dict_new",
                                [self.b.const(T.I64,
                                              len(node.keywords) + 1)])
            for kw in node.keywords:
                if kw.arg is None:
                    self.b.call(T.PTR, "apy_update",
                                [named, self._dyn_expr(kw.value)])
                else:
                    self.b.call(T.PTR, "apy_dict_set",
                                [named, self._dyn_str_literal(kw.arg),
                                 self._dyn_expr(kw.value)])
            out = self.b.call(T.PTR, "apy_str_format",
                              [receiver, packed, named])
            self._dyn_check()
            return out
        args = self._dyn_operands(node.args)
        sym = method_symbol(attr, len(args))
        if sym is not None and (attr in self.user_method_names
                                or self.extends_builtin):
            # THE NAME COLLIDES. `add` is a set's method and may equally be a
            # method of a class in this same program, and which one `x.add(1)`
            # means is decided by the receiver at run time -- there is no
            # static type here to ask. So both are emitted, behind a test.
            #
            # Only where a collision actually exists: the frontend knows every
            # method name every class in the module defines, so a program with
            # no `add` method keeps `s.add(1)` as one call and pays nothing.
            #
            # A CLASS EXTENDING A BUILTIN IS THE SAME SITUATION, arrived at
            # from the other side. `class D(dict)` INHERITS `keys` without
            # writing it, so the name is not in `user_method_names` and
            # `d.keys()` was lowered as a direct `apy_dict_keys(d)` -- handing
            # a runtime function that wants a dict an INSTANCE, which reported
            # `'D' object has no attribute 'keys'` for a method the object
            # plainly has. The receiver decides here too, so it gets the same
            # two-way shape; a program with no such class still pays nothing.
            return self._dyn_method_either(receiver, attr, args, sym,
                                           node.keywords)
        if sym is None:
            # Not a built-in method name: look the attribute up on the
            # receiver and call what comes back. This is the only path a user
            # class's method can take, and it is also what makes
            # `"abc".nosuch()` an AttributeError at run time -- where CPython
            # raises it -- rather than a compile error CPython does not have.
            attribute = self.b.call(T.PTR, "apy_getattr",
                                    [receiver, self._dyn_str_literal(attr)])
            self._dyn_check()
            return self._dyn_indirect(attribute, args, node.keywords)

        out = self._dyn_builtin_method(receiver, attr, args, sym,
                                       node.keywords)
        # A STR METHOD ON A BYTES RECEIVER answers bytes. The two share a
        # layout, so the operation is the same one -- only the tag on the
        # result differs, and doing it here covers every method at once rather
        # than changing each of the fifty-odd.
        #
        # Not gated on the symbol spelling: several of the shared methods
        # have symbols of their own (`apy_split_of`), and gating on
        # `apy_str_*` missed exactly those. `apy_str_like` returns its
        # argument untouched for any receiver that is not bytes.
        #
        # EXCEPT THE CONVERSIONS. `b.hex()` and `b.decode()` answer a str FROM
        # bytes -- that is what they are for -- so re-tagging their result
        # would undo the conversion the program asked for.
        if attr not in _BYTES_TO_TEXT:
            out = self.b.call(T.PTR, "apy_str_like", [receiver, out])
            self._dyn_check()
        return out

    def _dyn_builtin_method(self, receiver: int, attr: str, args: list,
                            sym: str, keywords=()) -> int:
        """One built-in method call, with the few irregular shapes spelled out."""
        if attr in DICT_PARTS:
            call_args = [receiver, self.b.const(T.I64, DICT_PARTS[attr])]
        elif attr == "pop" and len(args) < 2:
            # "no index" cannot be a sentinel value -- `xs.pop(-1)` is a real
            # call with a real index -- so the runtime is told separately.
            #
            # ONLY THE ONE-ARGUMENT SHAPES. `d.pop(k, default)` is a different
            # entry point taking the default as a VALUE, and this branch used
            # to claim every arity: it handed the flag `1` to the two-argument
            # symbol where a pointer belonged, and the program died
            # dereferencing it -- with its buffered output lost, so the
            # symptom was a program that printed nothing at all.
            index = args[0] if args else self.b.call(T.PTR, "apy_none", [])
            call_args = [receiver, index,
                         self.b.const(T.I64, 1 if args else 0)]
        elif attr == "update":
            # `d.update(other)`, `d.update(k=v)`, or both. The KEYWORDS become
            # a dict of their own and are applied after the positional one, so
            # a key given both ways takes the keyword -- which is what CPython
            # does and the only order that makes `d.update(d2, k=v)` useful.
            named = {kw.arg: kw.value for kw in keywords if kw.arg}
            call_args = [receiver, args[0]] if args else None
            if call_args is not None:
                self.b.call(T.PTR, sym, call_args)
                self._dyn_check()
            extra = self.b.call(T.PTR, "apy_dict_new",
                                [self.b.const(T.I64, len(named) + 1)])
            for key, value in named.items():
                self.b.call(T.PTR, "apy_dict_set",
                            [extra, self._dyn_str_literal(key),
                             self._dyn_expr(value)])
            call_args = [receiver, extra]
        elif attr in ("encode", "decode"):
            # THREE PARAMETERS ALWAYS: the receiver, the encoding and the
            # error handler. A call that named fewer is padded with None,
            # which the runtime reads as "the default" for each.
            call_args = [receiver]
            for i in range(2):
                call_args.append(args[i] if i < len(args)
                                 else self.b.call(T.PTR, "apy_none", []))
        elif attr in ("hex", "expandtabs") and not args:
            # The no-argument form. A separator of None means "none" and a
            # tab width of None means the default 8, which the runtime reads
            # off the kind rather than from a sentinel number.
            call_args = [receiver, self.b.call(T.PTR, "apy_none", [])]
        elif attr in ("get", "setdefault") and len(args) == 1:
            call_args = [receiver, args[0],
                         self.b.call(T.PTR, "apy_none", [])]
        elif attr == "sort":
            # `xs.sort()` is `sorted` in place, and takes the same two
            # keywords. Both travel as VALUES so the runtime calls the key
            # once per element, which is where the element ordering lives.
            named = {kw.arg: kw.value for kw in keywords if kw.arg}
            call_args = [
                receiver,
                (self._dyn_expr(named["key"]) if "key" in named
                 else self.b.call(T.PTR, "apy_none", [])),
                (self._dyn_expr(named["reverse"]) if "reverse" in named
                 else self.b.call(T.PTR, "apy_from_bool",
                                  [self.b.const(T.I64, 0)])),
            ]
        else:
            call_args = [receiver, *args]

        out = self.b.call(T.PTR, sym, call_args)
        self._dyn_check()
        return out

    @property
    def user_method_names(self) -> set:
        """Every method name any class in this module defines."""
        return {self.infos[k].node.name
                for c in self.classes.values() for k in c.methods}

    @property
    def extends_builtin(self) -> bool:
        """Whether any class here extends `dict`, `list`, `tuple`, `set` or
        `str`.

        WHICH ONE DOES NOT MATTER, only that one does. The question this
        answers is "can a receiver at an arbitrary call site be an instance
        carrying a builtin", and a single such class in the module makes the
        answer yes for every builtin method name at once -- there is nothing
        finer to test against, because the receiver's class is exactly what is
        not known at a dynamic call site.
        """
        return any(c.builtin_base is not None for c in self.classes.values())

    def _dyn_method_either(self, receiver: int, attr: str, args: list,
                           sym: str, keywords=()) -> int:
        """One call site, two answers, chosen by the receiver's kind.

        The result lands in ONE register written on both paths, which is what
        the mutable-register IR makes cheap -- under SSA this would need a phi.

        THE KEYWORDS TRAVEL WITH THE ARGUMENTS. They did not, and while this
        shape was reached only on a NAME COLLISION that was invisible: the
        colliding calls in the corpus passed none. Widening the test to cover
        a class extending a builtin brought ordinary calls through here, and
        `od.popitem(last=False)` quietly popped the other end -- the keyword
        was dropped and the default stood, which is a wrong answer with
        nothing to mark it.
        """
        out = self.b.reg(T.PTR)
        user = self.b.new_block("usermethod")
        builtin = self.b.new_block("builtinmethod")
        done = self.b.new_block("methodend")
        # THE CLASS DECIDES, NOT THE KIND. This used to ask
        # `apy_is_instance`, which sends every instance down the user path --
        # correct for a class that DEFINES the colliding name, and wrong for
        # one that INHERITS it from a builtin base, where the class has no
        # `keys` to find and the builtin call is never reached. See
        # `apy_method_is_builtin`.
        is_builtin = self.b.call(T.I64, "apy_method_is_builtin",
                                 [receiver, self._dyn_str_literal(attr)])
        self.b.branch(self.b.cmp(Op.NE, T.I64, is_builtin,
                                 self.b.const(T.I64, 0)), builtin, user)

        self.b.switch_to(user)
        found = self.b.call(T.PTR, "apy_getattr",
                            [receiver, self._dyn_str_literal(attr)])
        self._dyn_check()
        self.b.emit(Instruction(
            Op.COPY, T.PTR, dst=out,
            args=[self._dyn_indirect(found, args, keywords)]))
        self.b.jump(done)

        self.b.switch_to(builtin)
        # UNWRAPPED FIRST. A `class D(dict)` reaching `apy_dict_keys` has to
        # arrive as the dict it carries; handing the instance over is what
        # made the runtime report `'D' object has no attribute 'keys'`.
        self.b.emit(Instruction(
            Op.COPY, T.PTR, dst=out,
            args=[self._dyn_builtin_method(
                self.b.call(T.PTR, "apy_method_self",
                            [receiver, self._dyn_str_literal(attr)]),
                attr, args, sym, keywords)]))
        self.b.jump(done)

        self.b.switch_to(done)
        return out

    def _dyn_slice_value(self, sl: ast.Slice) -> int:
        """`a:b:c` built as an OBJECT, for the places one is a value.

        An omitted bound is None rather than a number: a `__getitem__` reading
        `key.start` has to be able to tell `c[1:]` from `c[0:]`, and every
        sentinel index is a real index for some sequence.
        """
        def part(expr):
            return (self.b.call(T.PTR, "apy_none", []) if expr is None
                    else self._dyn_expr(expr))
        out = self.b.call(T.PTR, "apy_slice_new",
                          [part(sl.lower), part(sl.upper), part(sl.step)])
        self._dyn_check()
        return out

    def _dyn_slice(self, node: ast.Subscript) -> int:
        """`xs[a:b:c]`, with each bound optional.

        Which bounds were WRITTEN is passed alongside their values, because an
        omitted bound is not the same as any number: for a negative step
        `xs[::-1]` starts at the end and `xs[0::-1]` at the front, and a
        sentinel like -1 would be indistinguishable from a real index.
        """
        sl = node.slice
        # EVERY PART OUTLIVES THE ONES AFTER IT. A bound may suspend, and the
        # sequence -- computed first -- is held in a register across all three
        # of them.
        parts = [sl.lower, sl.upper, sl.step]
        held = self._spill_across_await(self._dyn_expr(node.value), parts)

        def bound(expr, default, later):
            if expr is None:
                return self._holder(self.b.const(T.I64, default)), 0
            # THROUGH `apy_slice_bound`, NOT `apy_index`: a bound of
            # `2 ** 100` clamps where an INDEX of it refuses, and the
            # two cannot be told apart inside the converter.
            value = self.b.call(T.I64, "apy_slice_bound",
                                [self._dyn_expr(expr)])
            # AN i64, not a handle -- `_spill_across_await` stores through the
            # generator's object slots, which hold handles. Kept raw.
            return self._spill_raw(value, later), 1

        start, has_start = bound(sl.lower, 0, parts[1:])
        stop, has_stop = bound(sl.upper, 0, parts[2:])
        step = (self._holder(self.b.call(T.I64, "apy_slice_bound",
                                         [self._dyn_expr(sl.step)]))
                if sl.step is not None
                else self._holder(self.b.const(T.I64, 1)))
        out = self.b.call(T.PTR, "apy_slice",
                          [held(), start(), stop(), step(),
                           self.b.const(T.I64, has_start),
                           self.b.const(T.I64, has_stop)])
        self._dyn_check()
        return out

    # ── comprehensions and unpacking ────────────────────────────────────────
    def _dyn_unpack(self, target, value: int) -> None:
        """`a, b = pair` -- bind each name to one element, left to right.

        THE ARITY IS CHECKED FIRST, before anything is bound. A short sequence
        used to read past the end and report an IndexError from a subscript
        the program never wrote; a long one bound the leading names and
        silently dropped the rest, which is the worse of the two.
        """
        if isinstance(target, ast.Name):
            self._dyn_store(target.id, value)
            return
        if isinstance(target, ast.Starred):
            self._dyn_unpack(target.value, value)
            return
        if isinstance(target, ast.Attribute):
            # `a.x, a.y = pair`. An unpack target is any assignment target,
            # not only a name.
            self.b.call(T.PTR, "apy_setattr",
                        [self._dyn_expr(target.value),
                         self._dyn_attr_literal(target.attr), value])
            self._dyn_check()
            return
        if isinstance(target, ast.Subscript):
            self.b.call(T.PTR, "apy_setitem",
                        [self._dyn_expr(target.value),
                         self._dyn_expr(target.slice), value])
            self._dyn_check()
            return
        elts = list(target.elts)
        star = next((i for i, e in enumerate(elts)
                     if isinstance(e, ast.Starred)), None)
        # THROUGH `apy_iterable` FIRST, because the unpack below READS BY
        # INDEX -- and a generator has no indices. `a, b = g()` is ordinary
        # Python and reported that a generator is not subscriptable, which is
        # true of the lowering and not of the language. Anything already a
        # container comes back unchanged, so this costs nothing for the
        # `a, b = xs` that every program writes.
        value = self.b.call(T.PTR, "apy_iterable", [value])
        self._dyn_check()
        slot = self.b.alloca(8)
        self.b.store(T.PTR, value, slot)
        # A `*rest` turns the exact count into a FLOOR: `a, *b = xs` wants at
        # least one, and the message says so.
        self.b.call(T.PTR, "apy_unpack_check",
                    [value,
                     self.b.const(T.I64,
                                  len(elts) - (1 if star is not None else 0)),
                     self.b.const(T.I64, 1 if star is not None else 0)])
        self._dyn_check()

        def at(index: int) -> int:
            # A NEGATIVE index for the elements after a `*rest`, so the tail
            # is read from the end without ever computing the length: `a, *b,
            # c = xs` binds `c` from `xs[-1]` whatever `b` turned out to hold.
            item = self.b.call(T.PTR, "apy_getitem",
                               [self.b.load(T.PTR, slot),
                                self.b.call(T.PTR, "apy_from_int",
                                            [self.b.const(T.I64, index)])])
            self._dyn_check()
            return item

        if star is None:
            for i, elt in enumerate(elts):
                self._dyn_unpack(elt, at(i))
            return
        after = len(elts) - star - 1
        for i in range(star):
            self._dyn_unpack(elts[i], at(i))
        # `*rest` is a LIST, always -- `a, *rest = (1, 2, 3)` binds a list even
        # from a tuple, which is what `rest.append(...)` afterwards relies on.
        rest = self.b.call(T.PTR, "apy_slice",
                           [self.b.load(T.PTR, slot),
                            self.b.const(T.I64, star),
                            self.b.const(T.I64, -after),
                            self.b.const(T.I64, 1),
                            self.b.const(T.I64, 1),
                            self.b.const(T.I64, 1 if after else 0)])
        self._dyn_check()
        collected = self.b.call(T.PTR, "apy_list_new", [self.b.const(T.I64, 4)])
        self.b.call(T.PTR, "apy_extend", [collected, rest])
        self._dyn_check()
        self._dyn_unpack(elts[star], collected)
        for j in range(after):
            self._dyn_unpack(elts[star + 1 + j], at(-(after - j)))

    def _dyn_for_unpack(self, node: ast.For) -> None:
        """`for a, b in pairs:` -- the same loop, unpacking each element.

        One implementation rather than two: the target shape is the only
        difference, and keeping them apart is how the two drifted when the
        walk changed underneath.
        """
        self._dyn_for_sequence(node, "")

    def _comprehension(self, node, ctor: str, push: str = "apy_seq_push") -> int:
        """Pick the lowering by whether any clause is `async for`.

        The two differ in WHERE THE STATE LIVES, not in what they compute: an
        async one can suspend mid-loop, and a stack slot or a register does
        not survive the return that a suspension compiles to.

        AN `await` ANYWHERE INSIDE ONE SUSPENDS IT TOO, even with no `async
        for` clause: `[await f(v) for v in xs]` is an ordinary comprehension
        that parks in the middle. Deciding on the clauses alone sent it down
        the register path, and the resume read an accumulator no path had
        written -- invalid IR, reported against a block the program never
        wrote.
        """
        suspends = (any(gen.is_async for gen in node.generators)
                    or (self._gen is not None
                        and any(isinstance(n, ast.Await)
                                for n in ast.walk(node))))
        if suspends:
            return self._dyn_async_comprehension(node, ctor, push)
        return self._dyn_comprehension(node, ctor, push)

    def _dyn_async_comprehension(self, node, ctor: str,
                                 push: str = "apy_seq_push") -> int:
        """`[v async for v in agen()]`, and the set and dict forms.

        A SEPARATE LOWERING FROM THE ORDINARY ONE, and the reason is where the
        state lives. The plain comprehension keeps its accumulator in a stack
        slot and its loop index in a register, which is right until the loop
        can SUSPEND: a suspension compiles to a return from the step function,
        and neither survives it. Everything here goes in a frame slot instead.

        Clauses are walked recursively so `[x async for a in p() for x in a]`
        works, and a plain `for` inside an async comprehension keeps its index
        in a frame slot too -- an async clause further in would otherwise
        suspend across it and lose the count.
        """
        is_dict = isinstance(node, ast.DictComp)
        at_acc = self._gen_temp()
        self._gen_put(at_acc, self.b.call(T.PTR, ctor,
                                          [self.b.const(T.I64, 4)]))

        def emit(gens):
            if not gens:
                # THE ELEMENT FIRST, the accumulator after. Loading the
                # accumulator ahead of an element that suspends left the push
                # reading a register the resume path had never written --
                # which is the same rule the rest of this function follows and
                # the one place it was not.
                if is_dict:
                    key = self._dyn_expr(node.key)
                    val = self._dyn_expr(node.value)
                    self.b.call(T.PTR, "apy_dict_set",
                                [self._gen_get(at_acc), key, val])
                else:
                    item = self._dyn_expr(node.elt)
                    self.b.call(T.PTR, push, [self._gen_get(at_acc), item])
                self._dyn_check()
                return
            gen, rest = gens[0], gens[1:]
            skip = self.b.new_block("acompskip")
            if gen.is_async:
                at_src = self._gen_temp()
                self._gen_put(at_src, self._dyn_expr(gen.iter))
                test = self.b.new_block("acomptest")
                item_b = self.b.new_block("acompitem")
                susp = self.b.new_block("acompsusp")
                body = self.b.new_block("acompbody")
                done = self.b.new_block("acompend")
                self.b.jump(test)

                self.b.switch_to(test)
                at_item = self._gen_temp()
                self._gen_put(at_item, self.b.call(
                    T.PTR, "apy_agen_step", [self._gen_get(at_src)]))
                self._dyn_check()
                self.b.branch(
                    self.b.cmp(Op.EQ, T.PTR, self._gen_get(at_item),
                               self.b.call(T.PTR, "apy_stop", [])),
                    done, item_b)

                self.b.switch_to(item_b)
                self.b.branch(
                    self.b.cmp(Op.EQ, T.PTR, self._gen_get(at_item),
                               self.b.call(T.PTR, "apy_suspend_value", [])),
                    susp, body)

                # Outward, then RETRY THE SAME STEP -- no item was produced.
                self.b.switch_to(susp)
                self._dyn_yield_value(self._gen_get(at_item))
                self.b.jump(test)

                self.b.switch_to(body)
                if isinstance(gen.target, ast.Name):
                    self._dyn_store(gen.target.id, self._gen_get(at_item))
                else:
                    self._dyn_unpack(gen.target, self._gen_get(at_item))
                for cond in gen.ifs:
                    keep = self.b.new_block("acompkeep")
                    self.b.branch(self._dyn_truth(cond), keep, skip)
                    self.b.switch_to(keep)
                emit(rest)
                if self.b.current.terminator is None:
                    self.b.jump(skip)
                self.b.switch_to(skip)
                self.b.jump(test)
                self.b.switch_to(done)
                return

            # A PLAIN `for` clause, walked by index -- but with the sequence
            # and the counter in frame slots, because an async clause nested
            # inside this one suspends straight through here.
            at_seq = self._gen_temp()
            self._gen_put(at_seq, self.b.call(
                T.PTR, "apy_iterable", [self._dyn_expr(gen.iter)]))
            self._dyn_check()
            at_len, at_i = self._gen_temp(), self._gen_temp()
            self._gen_put(at_len, self.b.call(
                T.I64, "apy_raw_len", [self._gen_get(at_seq)]), raw=True)
            self._dyn_check()
            self._gen_put(at_i, self.b.const(T.I64, 0), raw=True)
            test = self.b.new_block("acomptest")
            body = self.b.new_block("acompbody")
            done = self.b.new_block("acompend")
            self.b.jump(test)
            self.b.switch_to(test)
            self.b.branch(self.b.cmp(Op.LT, T.I64,
                                     self._gen_get(at_i, raw=True),
                                     self._gen_get(at_len, raw=True)),
                          body, done)
            self.b.switch_to(body)
            item = self.b.call(T.PTR, "apy_key_at",
                               [self._gen_get(at_seq),
                                self._gen_get(at_i, raw=True)])
            self._dyn_check()
            if isinstance(gen.target, ast.Name):
                self._dyn_store(gen.target.id, item)
            else:
                self._dyn_unpack(gen.target, item)
            for cond in gen.ifs:
                keep = self.b.new_block("acompkeep")
                self.b.branch(self._dyn_truth(cond), keep, skip)
                self.b.switch_to(keep)
            emit(rest)
            if self.b.current.terminator is None:
                self.b.jump(skip)
            self.b.switch_to(skip)
            self._gen_put(at_i, self.b.add(T.I64,
                                           self._gen_get(at_i, raw=True),
                                           self.b.const(T.I64, 1)), raw=True)
            self.b.jump(test)
            self.b.switch_to(done)

        emit(node.generators)
        return self._gen_get(at_acc)

    def _dyn_comprehension(self, node, ctor: str,
                           push: str = "apy_seq_push") -> int:
        """A comprehension, lowered as the loop it is.

        Desugared here rather than rewritten into an ast.For and re-analysed,
        because the result has to be an EXPRESSION -- it appears mid-expression
        and the accumulator has to survive to the end of it. The accumulator
        lives in a stack slot rather than a register for the same reason the
        loop counter does: a register written inside a loop body and read after
        it is exactly what the memory-SSA style here avoids needing phis for.

        A generator expression is built eagerly as a list. That is a real
        divergence -- `type(x for x in y).__name__` is 'generator' and a
        generator is lazy and single-pass -- and it is what every use in the
        suite (a `for`, a `list()`, a `sum()`) cannot tell apart.
        """
        is_dict = isinstance(node, ast.DictComp)
        out = self.b.call(T.PTR, ctor, [self.b.const(T.I64, 4)])
        acc = self.b.alloca(8)
        self.b.store(T.PTR, out, acc)

        # A COMPREHENSION HAS ITS OWN SCOPE. `[i * 2 for i in range(3)]`
        # leaves the outer `i` alone, and a name only the comprehension binds
        # is unbound afterwards -- `list(j for j in xs)` then `j` is a
        # NameError.
        #
        # The body is still lowered in this frame, because the alternative is a
        # nested function and the closure machinery that comes with it. So each
        # TARGET IS SHADOWED: a fresh register stands in for its storage while
        # the comprehension runs, and the outer binding is never read or
        # written. Saving and restoring the real storage was the other way to
        # do it and it is wrong in a way the verifier catches: saving reads a
        # register no path has written when the name is otherwise unused.
        #
        # A walrus is deliberately NOT shadowed -- PEP 572 says it writes the
        # ENCLOSING scope, which is what `total := total + n` measures.
        # A CLASS BODY IS INVISIBLE INSIDE A COMPREHENSION. `[v * 2 for v
        # in values]` in a class body works because the OUTERMOST ITERABLE is
        # evaluated in the class scope; `[v * len(values) for v in range(2)]`
        # raises NameError, because everything else is evaluated in a scope of
        # the comprehension's own and a class body is not an enclosing scope
        # for it. Both halves are the same rule, and the difference is which
        # side of this line the expression sits on.
        outer_class_scope, outer_class_binds = self._class_scope, \
            self._class_binds
        first_iter = node.generators[0].iter if node.generators else None
        shadowed = []
        for gen in node.generators:
            for target in _target_names(gen.target):
                if target in self._shadow:
                    continue
                self._shadow[target] = self.b.reg(T.PTR)
                shadowed.append(target)

        def emit_generators(gens):
            if not gens:
                target = self.b.load(T.PTR, acc)
                if is_dict:
                    self.b.call(T.PTR, "apy_dict_set",
                                [target, self._dyn_expr(node.key),
                                 self._dyn_expr(node.value)])
                else:
                    self.b.call(T.PTR, push,
                                [target, self._dyn_expr(node.elt)])
                self._dyn_check()
                return
            gen, rest = gens[0], gens[1:]
            # THE OUTERMOST ITERABLE ONLY -- see the note above `shadowed`.
            if gen.iter is not first_iter:
                self._class_scope, self._class_binds = {}, None
            seq = self.b.call(T.PTR, "apy_iterable",
                              [self._dyn_expr(gen.iter)])
            self._class_scope, self._class_binds = {}, None
            self._dyn_check()
            slot = self.b.alloca(8)
            self.b.store(T.PTR, seq, slot)
            length = self.b.call(T.I64, "apy_raw_len", [seq])
            self._dyn_check()
            index = self.b.reg(T.I64)
            self.b.emit(Instruction(Op.COPY, T.I64, dst=index,
                                    args=[self.b.const(T.I64, 0)]))
            test = self.b.new_block("comptest")
            body = self.b.new_block("compbody")
            step = self.b.new_block("compstep")
            done = self.b.new_block("compend")
            self.b.jump(test)
            self.b.switch_to(test)
            self.b.branch(self.b.cmp(Op.LT, T.I64, index, length), body, done)
            self.b.switch_to(body)
            item = self.b.call(T.PTR, "apy_key_at",
                               [self.b.load(T.PTR, slot), index])
            self._dyn_check()
            if isinstance(gen.target, ast.Name):
                self._dyn_store(gen.target.id, item)
            else:
                self._dyn_unpack(gen.target, item)
            skip = self.b.new_block("compskip")
            for cond in gen.ifs:
                keep = self.b.new_block("compkeep")
                self.b.branch(self._dyn_truth(cond), keep, skip)
                self.b.switch_to(keep)
            emit_generators(rest)
            if self.b.current.terminator is None:
                self.b.jump(skip)
            self.b.switch_to(skip)
            self.b.jump(step)
            self.b.switch_to(step)
            nxt = self.b.reg(T.I64)
            self.b.emit(Instruction(Op.ADD, T.I64, dst=nxt,
                                    args=[index, self.b.const(T.I64, 1)]))
            self.b.emit(Instruction(Op.COPY, T.I64, dst=index, args=[nxt]))
            self.b.jump(test)
            self.b.switch_to(done)

        emit_generators(list(node.generators))
        self._class_scope, self._class_binds = outer_class_scope, \
            outer_class_binds
        for target in shadowed:
            del self._shadow[target]
        return self.b.load(T.PTR, acc)

    def _dyn_loop_else(self, node, broke) -> None:
        """The `else` clause of a loop, on the normal-exit path.

        Reached when the loop ran out rather than when a `break` left it --
        which is what `else` means on a loop and why `break` targets a
        different block. When there is no else clause the two blocks are the
        same one and this does nothing.
        """
        if not getattr(node, "orelse", None):
            return
        self._dyn_stmts(node.orelse)
        if self.b.current.terminator is None:
            self.b.jump(broke)
        self.b.switch_to(broke)
