"""Lowering for a function whose values are runtime objects.

A SEPARATE set of methods from the statically typed ones in `lower.py`, not a
flag threaded through them. The two share their control-flow shape and nothing
else: here every value is one opaque pointer and every operation is a call into
`link/objects.py`, where the value carries its own kind.

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
}
DYN_CMP = {
    ast.Eq: "apy_eq", ast.NotEq: "apy_ne", ast.Lt: "apy_lt",
    ast.LtE: "apy_le", ast.Gt: "apy_gt", ast.GtE: "apy_ge",
    ast.Is: "apy_is",
}
DYN_UNARY = {ast.USub: "apy_neg", ast.UAdd: "apy_pos", ast.Invert: "apy_invert"}
#: Builtins that are one call on one argument.
DYN_UNARY_BUILTIN = {
    "int": "apy_to_int", "float": "apy_to_float", "bool": "apy_to_bool",
    "str": "apy_str", "repr": "apy_repr", "len": "apy_len",
    "type": "apy_type_name", "sorted": "apy_sorted", "min": "apy_min",
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
    "ord": "apy_ord", "chr": "apy_chr", "ascii": "apy_repr",
    "bin": "apy_bin", "hex": "apy_hex", "oct": "apy_oct",
    "hash": "apy_hash", "callable": "apy_callable",
    "all": "apy_all", "any": "apy_any", "divmod": "apy_divmod",
    "hasattr": "apy_hasattr", "getattr": "apy_getattr",
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
}

_MULTI_BUILTINS = frozenset({"round", "int", "sum", "min", "max", "zip",
                             "iter", "pow", "format"})

#: The builtins with no fixed argument count, and the runtime call that takes
#: their arguments as a TUPLE. A value-form has no compile-time count, so its
#: thunk declares `*rest` and hands the tuple straight over -- which is what
#: makes `print` and `dict` usable as values at all.
_VARIADIC_THUNKS = {
    "print": "apy_print_seq", "dict": "apy_dict_of", "bytes": "apy_bytes_of",
}


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
        return self.b.call(T.PTR, "apy_from_cstr", [self._dyn_text_addr(text)])

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
                left = self._dyn_expr(node.left)
                right = self._dyn_expr(node.right)
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
            case ast.ListComp() | ast.GeneratorExp():
                return self._dyn_comprehension(node, "apy_list_new")
            case ast.SetComp():
                # A set is filled with `apy_set_push`, which DEDUPLICATES;
                # `apy_seq_push` would append and give `{2, 4, 4}`.
                return self._dyn_comprehension(node, "apy_set_new",
                                               "apy_set_push")
            case ast.DictComp():
                return self._dyn_comprehension(node, "apy_dict_new")
            case ast.Yield():
                return self._dyn_yield(node)
            case ast.YieldFrom():
                return self._dyn_yield_from(node)
            case ast.JoinedStr(values=parts):
                return self._dyn_fstring(parts)
            case ast.Set(elts=elts):
                out = self.b.call(T.PTR, "apy_set_new",
                                  [self.b.const(T.I64, max(1, len(elts)))])
                for element in elts:
                    if isinstance(element, ast.Starred):
                        self.b.call(T.PTR, "apy_set_update",
                                    [out, self._dyn_expr(element.value)])
                    else:
                        self.b.call(T.PTR, "apy_set_push",
                                    [out, self._dyn_expr(element)])
                    self._dyn_check()
                return out
            case ast.Dict(keys=keys, values=values):
                out = self.b.call(T.PTR, "apy_dict_new",
                                  [self.b.const(T.I64, max(1, len(keys)))])
                for k, v in zip(keys, values):
                    # Key then value, in source order: a display evaluates
                    # left to right and a later duplicate key overwrites.
                    self.b.call(T.PTR, "apy_dict_set",
                                [out, self._dyn_expr(k), self._dyn_expr(v)])
                    self._dyn_check()
                return out
            case ast.Subscript(slice=ast.Slice()):
                return self._dyn_slice(node)
            case ast.Subscript():
                out = self.b.call(T.PTR, "apy_getitem",
                                  [self._dyn_expr(node.value),
                                   self._dyn_expr(node.slice)])
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
        seq = self.b.call(T.PTR, sym, [self.b.const(T.I64, max(1, len(elts)))])
        for element in elts:
            if isinstance(element, ast.Starred):
                # `[*xs, y]`. The star flattens in place, so the capacity
                # guessed above is a lower bound rather than the answer --
                # which the container grows past on its own.
                self.b.call(T.PTR, "apy_extend",
                            [seq, self._dyn_expr(element.value)])
                self._dyn_check()
                continue
            self.b.call(T.PTR, "apy_seq_push", [seq, self._dyn_expr(element)])
        return seq

    def _dyn_fstring(self, parts: list) -> int:
        """An f-string, as a left-to-right chain of concatenations.

        Each interpolation goes through `str()` -- or `repr()` for `!r` --
        which is what an f-string does. A literal piece is a str constant like
        any other, so an f-string with no interpolations is exactly the string
        it looks like.
        """
        out = None
        for part in parts:
            if isinstance(part, ast.Constant):
                piece = self._dyn_str_literal(part.value)
            else:
                value = self._dyn_expr(part.value)
                # `!r` is repr, `!a` is ascii (repr here -- there is no
                # non-ASCII escaping yet), anything else and the default are
                # str. The conversion is a character code in the AST, and it
                # runs BEFORE the spec: `f"{x!r:>10}"` pads the repr.
                if part.conversion in (ord("r"), ord("a")):
                    value = self.b.call(T.PTR, "apy_repr", [value])
                    self._dyn_check()
                elif part.conversion == ord("s"):
                    value = self.b.call(T.PTR, "apy_str", [value])
                    self._dyn_check()
                # ALWAYS through `apy_format`, even with no spec: an
                # interpolation is `format(v, "")` and a class defining
                # `__format__` sees the empty spec rather than being sent to
                # `__str__` behind its back.
                spec = (self._dyn_fstring(part.format_spec.values)
                        if part.format_spec is not None
                        else self._dyn_str_literal(""))
                piece = self.b.call(T.PTR, "apy_format", [value, spec])
                self._dyn_check()
            if out is None:
                out = piece
            else:
                out = self.b.call(T.PTR, "apy_add", [out, piece])
                self._dyn_check()
        if out is None:
            return self._dyn_str_literal("")
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
        left = self._dyn_expr(node.left)
        last = len(node.ops) - 1
        for i, (op, right_node) in enumerate(zip(node.ops, node.comparators)):
            right = self._dyn_expr(right_node)
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
            left = right
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
        """Bind every module-level `def` to a function value, at program start.

        A module-level `def` is lifted out of the entry's body by analysis --
        it is a definition, not a statement to run -- so unlike a `class` there
        is no point in the body where its statement executes. Binding them all
        up front is the closest honest equivalent, and it differs from CPython
        only for a program that reads the NAME before the `def` textually
        appears, which CPython answers with a NameError and this answers with
        the function. Every direct call already ignores this binding; it exists
        so that `f` can be passed, stored and compared.
        """
        decorated = []
        for key, info in self.infos.items():
            if key == ENTRY_NAME or not info.dynamic or "." in key:
                continue
            func = self._dyn_function_value(key, key)
            if key in self.module_names:
                self._dyn_global_write(key, func)
            if getattr(info.node, "decorator_list", ()):
                decorated.append((key, info))
        # Decorators run in a SECOND pass, after every plain function value
        # exists, so `@twice` works when `twice` is written below the function
        # it decorates -- which is legal Python and common. Within the pass
        # they run in definition order, so a decorator that is itself
        # decorated has already been rebuilt.
        #
        # They run at program START rather than where the `def` textually is,
        # for the same reason the binding above does: analysis lifts a
        # module-level `def` out of the entry's body, so there is no statement
        # of its own to run at. A decorator that names something the module
        # body ASSIGNS -- `deco = make()` above an `@deco` -- is the case this
        # gets wrong, and it gets it wrong loudly, with a NameError.
        for key, info in decorated:
            value = self._dyn_decorated(info.node, self._dyn_load(key))
            if key in self.module_names:
                self._dyn_global_write(key, value)

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
        return self.b.call(T.PTR, "apy_func_new",
                           [code, self.b.const(T.I64, 1),
                            self._dyn_str_literal(name),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, 0),
                            self.b.const(T.I64, variadic)])

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
            self.b.ret(self._dyn_call(call))
            self.module.functions.append(fn)
            self.b, self.info = saved_b, saved_info

    def _dyn_star_args(self, node: ast.Call) -> int:
        """The argument list of a call containing `*xs`, built at run time.

        Each ordinary argument is appended, and each starred one is flattened
        in place, so `f(1, *xs, 2)` produces exactly the sequence CPython
        passes. The result is a list because the COUNT is not known here --
        that is what a star means -- and a list is the only shape that can
        carry a count decided at run time.
        """
        out = self.b.call(T.PTR, "apy_list_new",
                          [self.b.const(T.I64, max(1, len(node.args)))])
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                self.b.call(T.PTR, "apy_extend",
                            [out, self._dyn_expr(arg.value)])
                self._dyn_check()
            else:
                self.b.call(T.PTR, "apy_seq_push", [out, self._dyn_expr(arg)])
        return out

    def _dyn_spread_call(self, node: ast.Call) -> int:
        """`f(*xs)`, including `obj.m(*xs)`.

        The callee is evaluated as a VALUE rather than called directly: a
        direct call carries a fixed IR arity, and there is no number to give
        it. `apy_call_spread` then binds the list against the callee's own
        signature, so defaults, `*rest` and a wrong count all behave exactly
        as they do for an ordinary call -- reported at run time, which is
        where CPython reports them for this shape too.
        """
        if isinstance(node.func, ast.Attribute):
            callee = self.b.call(T.PTR, "apy_getattr",
                                 [self._dyn_expr(node.func.value),
                                  self._dyn_attr_literal(node.func.attr)])
            self._dyn_check()
        else:
            callee = self._dyn_expr(node.func)
        out = self.b.call(T.PTR, "apy_call_spread",
                          [callee, self._dyn_star_args(node)])
        self._dyn_check()
        return out

    def _dyn_arguments(self, node: ast.Call, info) -> list:
        """One value per parameter: positional, then keyword, then default.

        Resolved HERE rather than in the callee, because the signature is known
        at compile time -- so a keyword argument costs nothing at run time and
        the callee needs no notion of one.
        """
        params = [p.name for p in info.params]
        slots: list = [None] * len(params)
        for i, arg in enumerate(node.args[:len(params)]):
            slots[i] = self._dyn_expr(arg)
        by_name = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        for i, pname in enumerate(params):
            if slots[i] is None and pname in by_name:
                slots[i] = self._dyn_expr(by_name[pname])
        first_default = len(params) - len(info.defaults)
        for i, value in enumerate(slots):
            if value is None:
                slots[i] = self._dyn_default(info, i - first_default)
        if info.vararg is not None:
            extra = node.args[len(params):]
            rest = self.b.call(T.PTR, "apy_tuple_new",
                               [self.b.const(T.I64, max(1, len(extra)))])
            for arg in extra:
                self.b.call(T.PTR, "apy_seq_push", [rest, self._dyn_expr(arg)])
            slots.append(rest)
        return slots

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

        if name == "round" and n == 2:
            return done(self.b.call(T.PTR, "apy_round_to", [arg(0), arg(1)]))
        if name == "int" and n == 2:
            return done(self.b.call(T.PTR, "apy_to_int_base",
                                    [arg(0), arg(1)]))
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
        buf = self._dyn_argv(args)
        if not keywords:
            out = self.b.call(T.PTR, "apy_call",
                              [callee, buf, self.b.const(T.I64, len(args))])
            self._dyn_check()
            return out
        kwd = self.b.call(T.PTR, "apy_dict_new",
                          [self.b.const(T.I64, len(keywords) + 1)])
        for kw in keywords:
            if kw.arg is None:
                # `**d`, in SOURCE ORDER with the explicit keywords around it,
                # so a later one wins -- `f(**d, k=1)` and `f(k=1, **d)` are
                # different calls and CPython keeps the difference.
                self.b.call(T.PTR, "apy_update", [kwd, self._dyn_expr(kw.value)])
            else:
                self.b.call(T.PTR, "apy_dict_set",
                            [kwd, self._dyn_str_literal(kw.arg),
                             self._dyn_expr(kw.value)])
            self._dyn_check()
        out = self.b.call(T.PTR, "apy_call_kw",
                          [callee, buf, self.b.const(T.I64, len(args)), kwd])
        self._dyn_check()
        return out

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

    def _dyn_call(self, node: ast.Call) -> int:
        if any(isinstance(a, ast.Starred) for a in node.args):
            # `f(*xs)`. The argument COUNT is a value, so this cannot be a
            # direct call with a fixed IR arity -- the callee is evaluated as a
            # VALUE and the arguments are spread from a list built at run time.
            return self._dyn_spread_call(node)
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and (node.func.value.id, node.func.attr) in _TYPE_STATICS \
                and self.info.locals.get(node.func.value.id) is None:
            # `dict.fromkeys(...)` -- a constructor on the TYPE, with no
            # receiver. See `_TYPE_STATICS`.
            symbol, arity, defaults = _TYPE_STATICS[
                (node.func.value.id, node.func.attr)]
            args = [self._dyn_expr(a) for a in node.args[:arity]]
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
            args = [self._dyn_expr(a) for a in node.args[:arity]]
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
                                      [self._dyn_expr(a) for a in node.args],
                                      node.keywords)
        name = node.func.id
        if name == "super" and not node.args:
            return self._dyn_super()
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
            or any(kw.arg is None for kw in node.keywords))
        if name in self.class_names or by_value \
                or (name not in self.infos
                    and self._is_callable_value(name)):
            # A DECORATED function must be called through its NAME and not
            # through the direct symbol: `@deco def f` binds whatever `deco`
            # returned, and calling `pyf_f` would run the undecorated body --
            # a program that still works and does the wrong thing.
            # A class, or a local holding a callable. Both are values, and a
            # value is called through `apy_call`.
            return self._dyn_indirect(self._dyn_load(name),
                                      [self._dyn_expr(a) for a in node.args],
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
                              [self._dyn_expr(a) for a in node.args])
            self._dyn_check()
            return out
        if name == "complex":
            zero = self.b.call(T.PTR, "apy_from_complex",
                               [self.b.const(T.F64, 0.0),
                                self.b.const(T.F64, 0.0)])
            if not node.args:
                return zero
            real = self._dyn_expr(node.args[0])
            imag = (self._dyn_expr(node.args[1]) if len(node.args) > 1
                    else self.b.call(T.PTR, "apy_from_int",
                                     [self.b.const(T.I64, 0)]))
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
            return self._dyn_convert_sequence(node, name)
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
                else [self._dyn_expr(a) for a in node.args])
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
        values = [self._dyn_expr(a) for a in node.args]
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

    def _dyn_iterator(self, node) -> int:
        """What to step. One call, at the top of the loop, evaluating the
        iterable exactly once -- which is what makes `for x in f()` call `f`
        a single time."""
        got = self.b.call(T.PTR, "apy_getiter", [self._dyn_expr(node)])
        self._dyn_check()
        return got

    # ── statements ──────────────────────────────────────────────────────────
    def _dyn_stmts(self, body: list) -> None:
        for stmt in body:
            if self.b.current.terminator is not None:
                return          # dead code after return/break/continue
            self._dyn_stmt(stmt)

    def _dyn_stmt(self, node) -> None:
        self.b.span = self._span(node)
        match node:
            case ast.Expr():
                if not isinstance(node.value, ast.Constant):
                    self._dyn_expr(node.value)
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
            case ast.FunctionDef():
                self._dyn_store(node.name, self._dyn_decorated(
                    node, self._dyn_function_value(
                        self.def_keys[id(node)], node.name)))
            case ast.ClassDef():
                self._dyn_class(node)
            case ast.AnnAssign(target=ast.Name(id=name)):
                if node.value is not None:
                    self._dyn_bind(name, node.value)
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
            case ast.With():
                self._dyn_with(node)
            case ast.Try():
                self._dyn_try(node)
            case ast.Delete():
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._dyn_unbind(target.id)
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

    def _dyn_exception(self, node: ast.Call | ast.Name) -> int:
        """`ValueError('x')`, or a bare `ValueError`.

        A bare name is the same as calling it with no argument, which is what
        `raise ValueError` means -- CPython instantiates it for you.
        """
        if isinstance(node, ast.Name):
            name, args = node.id, []
        else:
            name, args = node.func.id, node.args
        # `E()` and `E(None)` are DIFFERENT exceptions -- `e.args` is `()` for
        # the first and `(None,)` for the second -- so the two go to different
        # constructors rather than both passing None.
        if not args:
            return self.b.call(T.PTR, "apy_make_exc0",
                               [self._dyn_str_literal(name)])
        return self.b.call(T.PTR, "apy_make_exc",
                           [self._dyn_str_literal(name),
                            self._dyn_expr(args[0])])

    def _dyn_raise(self, node: ast.Raise) -> None:
        if node.exc is None:
            # A bare `raise` re-raises whatever is current. The flag is still
            # set unless a handler cleared it, so there is nothing to rebuild.
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
        made = (self.b.call(T.PTR, "apy_make_exc",
                            [self._dyn_str_literal("AssertionError"),
                             self._dyn_expr(node.msg)])
                if node.msg is not None
                else self.b.call(T.PTR, "apy_make_exc0",
                                 [self._dyn_str_literal("AssertionError")]))
        self.b.call(T.PTR, "apy_raise", [made])
        self._dyn_check_forced()
        if self.b.current.terminator is None:
            self.b.jump(ok)
        self.b.switch_to(ok)

    def _dyn_with(self, node: ast.With) -> None:
        """`with a as x, b as y: body`.

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
        entered = self.b.call(T.PTR, "apy_enter", [manager])
        self._dyn_check()
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
            self.b.call(T.PTR, "apy_exit", [held(), none])
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
            inner = ast.With(items=rest, body=node.body)
            ast.copy_location(inner, node)
            self._dyn_with(inner)
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
        exc = self.b.call(T.PTR, "apy_error_value", [])
        self.b.call(T.VOID, "apy_error_clear", [])
        # The original is WHAT IS BEING HANDLED while `__exit__` runs, so an
        # exception raised inside it chains to this one -- which is what
        # `e.__context__` reports and the only way to tell the two apart.
        was = self.b.call(T.PTR, "apy_error_handling", [exc])
        swallowed = self.b.call(T.PTR, "apy_exit", [held(), exc])
        self.b.call(T.PTR, "apy_error_handling", [was])
        self._dyn_check()
        keep = self.b.new_block("withreraise")
        self.b.branch(self.b.cmp(Op.NE, T.I64,
                                 self.b.call(T.I64, "apy_truth", [swallowed]),
                                 self.b.const(T.I64, 0)),
                      done, keep)
        self.b.switch_to(keep)
        self.b.call(T.PTR, "apy_raise", [exc])
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
            self._dyn_stmts(handler.body)
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
        value = self._dyn_expr(node.value)
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
            self._dyn_stmts(pending)

    def _dyn_finally(self, node: ast.Try) -> None:
        if not node.finalbody:
            return
        self._dyn_stmts(node.finalbody)

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

    def _dyn_function_value(self, key: str, name_text: str) -> int:
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
        # The parameter NAMES, for a keyword argument passed through a value.
        # A direct call matches them at compile time and never reads these; a
        # call through `apy_call` reaches a function whose `def` the caller
        # never saw, and without them `C(1, swallow=True)` had nowhere to look
        # and quietly took the default.
        for i, param in enumerate(info.params):
            self.b.call(T.PTR, "apy_func_param",
                        [func, self.b.const(T.I64, i),
                         self._dyn_str_literal(param.name)])
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

    def _dyn_member(self, module: str, attr: str) -> int:
        """One member of a builtin module, as a value.

        A constant is a literal; a function becomes a callable of the right
        arity, so `math.sqrt(2)` and `sorted(xs, key=math.sqrt)` are the same
        object reached two ways.
        """
        kind, *payload = member(module, attr)
        if kind == "str":
            return self._dyn_str_literal(payload[0])
        if kind == "float":
            return self.b.call(T.PTR, "apy_from_float",
                               [self.b.const(T.F64, payload[0])])
        if kind == "int":
            return self.b.call(T.PTR, "apy_from_int",
                               [self.b.const(T.I64, payload[0])])
        return self._dyn_native_value(payload[0], payload[1], attr,
                                      *payload[2:])

    def _dyn_native_value(self, symbol: str, arity: int, name: str,
                          params=(), defaults=()) -> int:
        """A runtime function as a CALLABLE VALUE, via a synthesised wrapper.

        The same shape `_dyn_builtin_value` builds for `len` and `repr`, with
        an arity that is not always one -- `math.gcd` takes two. One wrapper
        per symbol per module, emitted on first use, so `math.sqrt` written
        twice is one function and two references to it.
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
                            self.b.const(T.I64, 0)])
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

    def _dyn_emit_natives(self) -> None:
        """Emit the body of every native wrapper this module asked for.

        Drained after the real functions, like the builtin thunks and for the
        same reason: asking for one is what creates it, and that happens while
        lowering something else.
        """
        i = 0
        while i < len(self._pending_natives):
            symbol, wrapper, arity = self._pending_natives[i]
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
            out = self.b.call(T.PTR, symbol, args)
            self.b.ret(out)
            self.module.functions.append(fn)
            self.b, self.info = saved_b, saved_info

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
        self._gen_put(at_src, source)
        seq = self.b.call(T.PTR, "apy_iterable", [source])
        self._dyn_check()
        at_seq, at_len, at_i = (self._gen_temp(), self._gen_temp(),
                                self._gen_temp())
        self._gen_put(at_seq, seq)
        length = self.b.call(T.I64, "apy_raw_len", [seq])
        self._dyn_check()
        self._gen_put(at_len, length, raw=True)
        self._gen_put(at_i, self.b.const(T.I64, 0), raw=True)
        test = self.b.new_block("yftest")
        body = self.b.new_block("yfbody")
        done = self.b.new_block("yfend")
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
        self._dyn_yield_value(item)
        nxt = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.ADD, T.I64, dst=nxt,
                                args=[self._gen_get(at_i, raw=True),
                                      self.b.const(T.I64, 1)]))
        self._gen_put(at_i, nxt, raw=True)
        self.b.jump(test)

        self.b.switch_to(done)
        return self.b.call(T.PTR, "apy_gen_taken", [self._gen_get(at_src)])

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
            value = self._dyn_indirect(self._dyn_expr(deco), [value])
        return value

    def _dyn_class(self, node) -> None:
        """`class C(B): ...` -- build the type, fill it, bind the name."""
        key = self.class_of_node[id(node)]
        info = self.classes[key]
        if info.is_exception:
            # A name in the exception hierarchy, registered where the `class`
            # statement runs. Nothing is bound: `MyError` is not a value, it
            # is a name `raise` and `except` both resolve.
            self.b.call(T.PTR, "apy_exc_register",
                        [self._dyn_str_literal(info.name),
                         self._dyn_str_literal(info.base)])
            self._dyn_check()
            return
        base = (self._dyn_load(info.base) if info.base is not None
                else self.b.call(T.PTR, "apy_none", []))
        cls = self.b.call(T.PTR, "apy_type_new",
                          [self._dyn_str_literal(info.name), base])
        self._dyn_check()
        # The class body's bindings, in SOURCE order: a later `def` of the
        # same name replaces an earlier one, exactly as re-assigning does.
        for name, value in info.attrs:
            self.b.call(T.PTR, "apy_type_set",
                        [cls, self._dyn_str_literal(
                            self._mangled(name, info.name)),
                         self._dyn_expr(value)])
            self._dyn_check()
        for mkey in info.methods:
            minfo = self.infos[mkey]
            self.b.call(T.PTR, "apy_type_set",
                        [cls, self._dyn_str_literal(
                            self._mangled(minfo.node.name, info.name)),
                         self._dyn_decorated(
                             minfo.node,
                             self._dyn_function_value(mkey,
                                                      minfo.node.name))])
            self._dyn_check()
        self._dyn_store(node.name, self._dyn_decorated(node, cls))

    def _is_callable_value(self, name: str) -> bool:
        """Whether `name` holds a callable VALUE rather than naming a `def`.

        A local, a module global, or a class. Checked before the builtin table
        so that a program which binds `sorted` to something of its own gets
        its own -- which is what Python does, and what the builtin path would
        silently override.
        """
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
        if self._is_module_name(name):
            return self._dyn_global_read(name)
        sym = self.info.locals.get(name)
        if sym is None and name == "Ellipsis":
            return self.b.call(T.PTR, "apy_ellipsis", [])
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

    def _is_module_name(self, name: str) -> bool:
        """True when `name` refers to the module's storage rather than a local.

        Two ways in: the entry function, where every name IS a module name, and
        any function that either read it as a global or declared it one.
        """
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
        args = [self._dyn_expr(a) for a in node.args]
        sym = method_symbol(attr, len(args))
        if sym is not None and attr in self.user_method_names:
            # THE NAME COLLIDES. `add` is a set's method and may equally be a
            # method of a class in this same program, and which one `x.add(1)`
            # means is decided by the receiver at run time -- there is no
            # static type here to ask. So both are emitted, behind a test.
            #
            # Only where a collision actually exists: the frontend knows every
            # method name every class in the module defines, so a program with
            # no `add` method keeps `s.add(1)` as one call and pays nothing.
            return self._dyn_method_either(receiver, attr, args, sym)
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

        return self._dyn_builtin_method(receiver, attr, args, sym,
                                        node.keywords)

    def _dyn_builtin_method(self, receiver: int, attr: str, args: list,
                            sym: str, keywords=()) -> int:
        """One built-in method call, with the few irregular shapes spelled out."""
        if attr in DICT_PARTS:
            call_args = [receiver, self.b.const(T.I64, DICT_PARTS[attr])]
        elif attr == "pop":
            # "no index" cannot be a sentinel value -- `xs.pop(-1)` is a real
            # call with a real index -- so the runtime is told separately.
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
        elif attr in ("hex", "expandtabs", "encode", "decode") and not args:
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

    def _dyn_method_either(self, receiver: int, attr: str, args: list,
                           sym: str) -> int:
        """One call site, two answers, chosen by the receiver's kind.

        The result lands in ONE register written on both paths, which is what
        the mutable-register IR makes cheap -- under SSA this would need a phi.
        """
        out = self.b.reg(T.PTR)
        user = self.b.new_block("usermethod")
        builtin = self.b.new_block("builtinmethod")
        done = self.b.new_block("methodend")
        is_inst = self.b.call(T.I64, "apy_is_instance", [receiver])
        self.b.branch(self.b.cmp(Op.NE, T.I64, is_inst,
                                 self.b.const(T.I64, 0)), user, builtin)

        self.b.switch_to(user)
        found = self.b.call(T.PTR, "apy_getattr",
                            [receiver, self._dyn_str_literal(attr)])
        self._dyn_check()
        self.b.emit(Instruction(Op.COPY, T.PTR, dst=out,
                                args=[self._dyn_indirect(found, args)]))
        self.b.jump(done)

        self.b.switch_to(builtin)
        self.b.emit(Instruction(
            Op.COPY, T.PTR, dst=out,
            args=[self._dyn_builtin_method(receiver, attr, args, sym)]))
        self.b.jump(done)

        self.b.switch_to(done)
        return out

    def _dyn_slice(self, node: ast.Subscript) -> int:
        """`xs[a:b:c]`, with each bound optional.

        Which bounds were WRITTEN is passed alongside their values, because an
        omitted bound is not the same as any number: for a negative step
        `xs[::-1]` starts at the end and `xs[0::-1]` at the front, and a
        sentinel like -1 would be indistinguishable from a real index.
        """
        sl = node.slice
        seq = self._dyn_expr(node.value)

        def bound(expr, default):
            if expr is None:
                return self.b.const(T.I64, default), 0
            value = self.b.call(T.I64, "apy_index", [self._dyn_expr(expr)])
            return value, 1

        start, has_start = bound(sl.lower, 0)
        stop, has_stop = bound(sl.upper, 0)
        step = (self.b.call(T.I64, "apy_index", [self._dyn_expr(sl.step)])
                if sl.step is not None else self.b.const(T.I64, 1))
        out = self.b.call(T.PTR, "apy_slice",
                          [seq, start, stop, step,
                           self.b.const(T.I64, has_start),
                           self.b.const(T.I64, has_stop)])
        self._dyn_check()
        return out

    # ── comprehensions and unpacking ────────────────────────────────────────
    def _dyn_unpack(self, target, value: int) -> None:
        """`a, b = pair` -- bind each name to one element, left to right.

        The length is NOT checked. CPython raises "too many values to unpack",
        and this instead reads past the end and gets an IndexError from
        `apy_getitem`, whose message names the wrong thing. Stated rather than
        hidden: it needs a length compare and a raise, which is cheap, and
        every use in the suite unpacks the right arity.
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
        slot = self.b.alloca(8)
        self.b.store(T.PTR, value, slot)

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
            seq = self.b.call(T.PTR, "apy_iterable",
                              [self._dyn_expr(gen.iter)])
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
