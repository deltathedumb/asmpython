"""Scopes, symbols, and the type rules the parser calls into.

WHAT LIVES HERE AND WHAT LIVES IN `parser.py`: the parser knows the grammar and
nothing about what `+` means; this module knows what `+` means and nothing about
where the `+` was written. So `build_binary` takes two typed expressions and an
operator and returns a typed expression or reports -- and can be read, and
tested, without a token in sight.

FOUR NAMESPACES, WHICH IS ONE OF C'S REAL SURPRISES. `struct s { int s; };
enum { s }; s: ;` declares four different `s` in one function and every one is
legal. Tags, ordinary identifiers, labels and struct members are separate name
spaces (6.2.3) and `Scope` keeps the first three apart. Members belong to their
tag and are looked up through it.

CONSTANT FOLDING IS NOT AN OPTIMISATION HERE. An array bound, a case label, an
enumeration value, a bit-field width and a static initialiser are all required
by the language to be constants, so `fold` is part of type checking rather than
something a later pass might do -- and its failure is a diagnostic about the
program rather than a missed opportunity.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from ...diagnostics import DiagnosticSink, Span, error, warning
from . import ctype as C
from .ctype import CType
from . import syntax as S


class Storage(enum.Enum):
    AUTO = "auto"
    REGISTER = "register"
    STATIC = "static"
    EXTERN = "extern"
    TYPEDEF = "typedef"
    ENUM_CONST = "enumeration constant"
    #: A parameter. Automatic storage, but the frame slot is filled on entry
    #: rather than by an initialiser, which lowering needs to tell apart.
    PARAM = "parameter"


class Linkage(enum.Enum):
    NONE = "none"
    INTERNAL = "internal"
    EXTERNAL = "external"


@dataclass(slots=True)
class Symbol:
    """One declared name."""

    name: str
    type: CType
    storage: Storage
    linkage: Linkage = Linkage.NONE
    span: Span | None = None
    #: True once a definition (not merely a declaration) has been seen.
    defined: bool = False
    #: ENUM_CONST only.
    value: int = 0
    #: The name the IR uses. Equal to `name` for anything with linkage; a
    #: file-scope `static` or a block-scope `static` gets a unique one, because
    #: two functions may each have a `static int count;` and the IR has one
    #: flat namespace of globals.
    ir_name: str = ""
    #: Set by lowering: the register holding this local's ADDRESS, or None for
    #: a local kept in a register.
    slot: Any = None
    #: A local that is never addressed and is not an aggregate can live in a
    #: register instead of a frame slot. Set by the parser when it sees `&x`.
    addressed: bool = False
    #: True for a function whose body this unit defines.
    has_body: bool = False
    #: The initialiser of a static object, resolved.
    init: Any = None
    #: True if the symbol was declared `inline` without `extern`, so an
    #: unused definition need not be emitted.
    inline: bool = False
    #: `_Thread_local`: one copy per thread rather than one for the program.
    #: Lowering turns every use into a lookup -- see `lower._tls_address`.
    thread_local: bool = False
    #: True while the symbol is a function parameter of a VLA type, whose
    #: size must be evaluated on entry.
    vla_bound: Any = None
    #: `_Alignas`, when it asked for more than the type's own alignment.
    align: int | None = None
    #: Set by lowering: True when the object lives in an IR register rather
    #: than in frame storage. Only a scalar whose address is never taken can.
    in_register: bool = False
    #: `constexpr`: the object's value, folded, so that the NAME is a
    #: constant expression -- which is the whole of what the specifier
    #: buys over `const`. None for an object that is not one, and for one
    #: whose value is not a number (an aggregate is still constant, but
    #: its name is not usable where an integer constant is asked for).
    const_value: Any = None
    #: The C23 `[[...]]` attributes this name was declared with, standard
    #: ones only. `attributes.py` says which of them do anything; the two
    #: that do are `nodiscard` and `deprecated`, and both are warnings at
    #: the point of USE, which is why they have to live on the symbol.
    attrs: frozenset[str] = frozenset()

    @property
    def is_global(self) -> bool:
        return self.storage in (Storage.STATIC, Storage.EXTERN)


class Scope:
    """One block. Ordinary names, tags and labels kept apart."""

    __slots__ = ("parent", "names", "tags", "is_file", "is_function_prototype")

    def __init__(self, parent: Scope | None, *, file: bool = False,
                 prototype: bool = False) -> None:
        self.parent = parent
        self.names: dict[str, Symbol] = {}
        self.tags: dict[str, C.Tag] = {}
        self.is_file = file
        self.is_function_prototype = prototype

    def lookup(self, name: str) -> Symbol | None:
        scope: Scope | None = self
        while scope is not None:
            got = scope.names.get(name)
            if got is not None:
                return got
            scope = scope.parent
        return None

    def lookup_tag(self, name: str, *, here: bool = False) -> C.Tag | None:
        scope: Scope | None = self
        while scope is not None:
            got = scope.tags.get(name)
            if got is not None:
                return got
            if here:
                return None
            scope = scope.parent
        return None

    def declare(self, sym: Symbol) -> None:
        self.names[sym.name] = sym

    def declare_tag(self, tag: C.Tag) -> None:
        if tag.name:
            self.tags[tag.name] = tag


# ── the null pointer constant ───────────────────────────────────────────────
def is_null_constant(e: S.Expr) -> bool:
    """`0`, `0L`, `'\\0'` or any of those cast to `void *`. 6.3.2.3p3.

    NOT `(char *)0`, and not a `const int` variable that happens to be zero:
    the rule names an integer constant expression with the value 0, optionally
    cast to `void *`, and widening it is how `f(0)` starts matching a pointer
    parameter that the programmer meant to pass an integer to.
    """
    inner = e
    while isinstance(inner, (S.Cast, S.Conv)):
        if isinstance(inner, S.Cast):
            t = inner.type
            if not (t.is_pointer and t.of.unqualified().is_void):
                return False
        inner = inner.operand
    return (isinstance(inner, S.IntLit) and inner.value == 0
            and inner.type.is_integer)


class Sema:
    """The type rules. One instance per translation unit."""

    def __init__(self, sink: DiagnosticSink, prefix: str = "c0.") -> None:
        self.sink = sink
        self.file_scope = Scope(None, file=True)
        self.scope = self.file_scope
        #: WHAT THIS UNIT'S OWN NAMES ARE CALLED IN THE IR. A `static` is not
        #: the platform's name and must not become one (see `parser._merge`),
        #: and with several translation units in one build it is not the
        #: OTHER unit's either: two files may each have a `static int count`,
        #: and the IR has one flat namespace of globals. So each unit has a
        #: prefix of its own -- `c0.`, `c1.` -- and `c.` is the bundled
        #: library's, shared by every unit exactly as one `libc.a` is.
        self.prefix = prefix
        #: Names already given to IR globals, so a `static` inside a function
        #: and a file-scope one of the same name cannot collide.
        self.taken: set[str] = set()
        self.tags: list[C.Tag] = []

    # ── reporting ───────────────────────────────────────────────────────────
    def error(self, code: str, message: str, span: Span, *,
              note: str = "", help: str = "", also=None) -> None:
        d = error(code, message).at(span)
        if also is not None:
            d = d.also(also[0], also[1])
        if note:
            d = d.note(note)
        if help:
            d = d.help(help)
        self.sink.report(d)

    def warn(self, code: str, message: str, span: Span, *,
             note: str = "", help: str = "") -> None:
        d = warning(code, message).at(span)
        if note:
            d = d.note(note)
        if help:
            d = d.help(help)
        self.sink.report(d)

    def poison(self, span: Span) -> S.Expr:
        """A stand-in for an expression that failed to type.

        `int` rather than a special error type, and it is deliberate: the
        parser keeps going, and an error type propagates into a second
        diagnostic at every operator it touches. One report per mistake is
        the goal, so the recovery value participates in arithmetic silently.
        """
        return S.IntLit(span, C.INT, False, 0)

    # ── scopes ──────────────────────────────────────────────────────────────
    def push(self, *, prototype: bool = False) -> Scope:
        self.scope = Scope(self.scope, prototype=prototype)
        return self.scope

    def pop(self) -> None:
        assert self.scope.parent is not None, "popped the file scope"
        self.scope = self.scope.parent

    def unique(self, base: str) -> str:
        name = base
        n = 0
        while name in self.taken:
            n += 1
            name = f"{base}.{n}"
        self.taken.add(name)
        return name

    # ── conversions ─────────────────────────────────────────────────────────
    def decay(self, e: S.Expr) -> S.Expr:
        """Array-to-pointer and function-to-pointer, as a node.

        An array's decay is NOT a no-op at the IR level in general -- but here
        it is, because an array lvalue is already represented by its address.
        The node exists so the tree says a conversion happened.
        """
        if e.type.is_array or e.type.is_function:
            return S.Conv(e.span, C.decay(e.type), False, e, "decay")
        return e

    def lvalue_conversion(self, e: S.Expr) -> S.Expr:
        """Reading an lvalue: drops the qualifiers, decays arrays and
        functions, and is what makes `const int x` usable as an `int`."""
        e = self.decay(e)
        if e.lvalue and e.type.qual:
            return S.Conv(e.span, e.type.unqualified(), False, e, "lvalue")
        return e

    def convert(self, e: S.Expr, target: CType, reason: str) -> S.Expr:
        """An implicit conversion to `target`, as a node. No checking."""
        e = self.decay(e)
        if C.compatible(e.type.unqualified(), target.unqualified()) and \
                not e.lvalue:
            return e
        return S.Conv(e.span, target.unqualified(), False, e, reason)

    def assignable(self, target: CType, value: S.Expr, what: str,
                   span: Span) -> S.Expr | None:
        """Check an assignment's right-hand side and convert it. 6.5.16.1.

        Returns the converted expression, or None having reported. `what` is
        "assignment", "argument 2 of `f`", "return", "initialisation" -- the
        four contexts that share these rules, named so the message does.
        """
        value = self.lvalue_conversion(value)
        t, v = target.unqualified(), value.type.unqualified()
        if t.is_arithmetic and v.is_arithmetic:
            return self.convert(value, target, what)
        if t.is_bool and v.is_pointer:
            return self.convert(value, target, what)
        if t.is_record and v.is_record and t.tag is v.tag:
            return value
        if t.is_pointer and is_null_constant(value):
            return self.convert(value, target, what)
        if t.is_pointer and v.is_pointer:
            a, b = t.of, v.of
            if not (a.unqualified().is_void or b.unqualified().is_void
                    or C.compatible(a.unqualified(), b.unqualified())):
                self.warn(
                    "W1402",
                    f"{what} makes {C.spell(target)} from {C.spell(value.type)}",
                    span,
                    note="the pointed-to types are not compatible",
                    help="add an explicit cast if the conversion is intended")
            elif b.qual - a.qual:
                lost = ", ".join(sorted(b.qual - a.qual))
                self.warn(
                    "W1403", f"{what} discards `{lost}` qualifier", span,
                    note=f"from {C.spell(value.type)} to {C.spell(target)}")
            return self.convert(value, target, what)
        if t.is_pointer and v.is_integer:
            self.warn("W1404",
                      f"{what} makes {C.spell(target)} from {C.spell(value.type)} "
                      f"without a cast", span)
            return self.convert(value, target, what)
        if t.is_integer and v.is_pointer:
            self.warn("W1404",
                      f"{what} makes {C.spell(target)} from {C.spell(value.type)} "
                      f"without a cast", span)
            return self.convert(value, target, what)
        if t.is_void and v.is_void:
            return value
        self.error("E1401",
                   f"cannot use {C.spell(value.type)} in {what} of "
                   f"{C.spell(target)}", span)
        return None

    # ── lvalue rules ────────────────────────────────────────────────────────
    def modifiable(self, e: S.Expr, what: str) -> bool:
        """Whether `e` may be assigned to. 6.3.2.1p1."""
        if not e.lvalue:
            self.error("E1405", f"{what} needs an lvalue", e.span,
                       note="only a named object, a dereference, a member or "
                            "a subscript can be assigned to")
            return False
        t = e.type
        if t.is_array:
            self.error("E1406", f"cannot {what} an array", e.span,
                       help="assign to its elements, or use memcpy")
            return False
        if t.is_const:
            self.error("E1407", f"cannot {what} a `const` object", e.span,
                       note=f"it has type {C.spell(t)}")
            return False
        if t.is_record and _has_const_member(t):
            self.error("E1407",
                       f"cannot {what}: {C.spell(t)} has a `const` member",
                       e.span)
            return False
        if not t.complete:
            self.error("E1408", f"cannot {what} an incomplete {C.spell(t)}",
                       e.span)
            return False
        return True

    # ── building expressions ────────────────────────────────────────────────
    def build_unary(self, op: str, operand: S.Expr, span: Span) -> S.Expr:
        if op == "&":
            return self._address_of(operand, span)
        if op == "*":
            return self._dereference(operand, span)
        if op in ("++", "--"):
            return self._incdec(op, operand, span, postfix=False)
        e = self.lvalue_conversion(operand)
        if op in ("__real__", "__imag__"):
            if not e.type.is_arithmetic:
                self.error("E1409",
                           f"`{op}` needs an arithmetic operand, not "
                           f"{C.spell(e.type)}", span)
                return self.poison(span)
            # A REAL OPERAND HAS AN IMAGINARY PART, and it is zero. gcc says
            # so, and it is what makes these usable in a macro that does not
            # know which it was given.
            half = e.type.of if e.type.is_complex else C.promote(e.type)
            return S.Unary(span, half, False, op, e)
        if op in ("+", "-"):
            if not e.type.is_arithmetic:
                self.error("E1409",
                           f"unary `{op}` needs an arithmetic operand, not "
                           f"{C.spell(e.type)}", span)
                return self.poison(span)
            t = C.promote(e.type)
            e = self.convert(e, t, "promotion")
            return S.Unary(span, t, False, op, e) if op == "-" else e
        if op == "~":
            if not e.type.is_integer:
                self.error("E1409",
                           f"`~` needs an integer operand, not "
                           f"{C.spell(e.type)}", span)
                return self.poison(span)
            t = C.promote(e.type)
            return S.Unary(span, t, False, op, self.convert(e, t, "promotion"))
        if op == "!":
            if not e.type.is_scalar:
                self.error("E1409",
                           f"`!` needs a scalar operand, not {C.spell(e.type)}",
                           span)
                return self.poison(span)
            return S.Unary(span, C.INT, False, op, e)
        raise AssertionError(op)

    def _address_of(self, operand: S.Expr, span: Span) -> S.Expr:
        if operand.type.is_function:
            return S.Unary(span, C.pointer_to(operand.type), False, "&", operand)
        if isinstance(operand, S.MemberAccess) and operand.path and \
                operand.path[-1].is_bitfield:
            self.error("E1410", "cannot take the address of a bit-field", span,
                       note="a bit-field has no address of its own")
            return self.poison(span)
        if not operand.lvalue:
            self.error("E1411", "cannot take the address of this expression",
                       span,
                       note="`&` needs an object with a location in memory")
            return self.poison(span)
        self._mark_addressed(operand)
        return S.Unary(span, C.pointer_to(operand.type), False, "&", operand)

    def mark_addressed(self, e: S.Expr) -> None:
        self._mark_addressed(e)

    def _mark_addressed(self, e: S.Expr) -> None:
        """Remember that a local is addressed, so lowering gives it a slot."""
        while True:
            if isinstance(e, S.Ident):
                if e.sym is not None:
                    e.sym.addressed = True
                return
            if isinstance(e, (S.MemberAccess,)) and not e.arrow:
                e = e.base
                continue
            if isinstance(e, S.Conv):
                e = e.operand
                continue
            return

    def _dereference(self, operand: S.Expr, span: Span) -> S.Expr:
        e = self.decay(operand)
        if not e.type.is_pointer:
            self.error("E1412",
                       f"cannot dereference {C.spell(e.type)}", span,
                       note="`*` needs a pointer")
            return self.poison(span)
        target = e.type.of
        if target.is_function:
            # `*f` where f is a function pointer is the function itself, and
            # `(*f)(x)` and `f(x)` are the same call. Returning the pointer
            # keeps that true without a special case at the call site.
            return e
        return S.Unary(span, target, True, "*", e)

    def _incdec(self, op: str, operand: S.Expr, span: Span,
                postfix: bool) -> S.Expr:
        what = "increment" if op == "++" else "decrement"
        if not self.modifiable(operand, what):
            return self.poison(span)
        t = operand.type
        if t.is_pointer:
            if not t.of.complete:
                self.error("E1413",
                           f"cannot {what} a pointer to the incomplete type "
                           f"{C.spell(t.of)}", span)
                return self.poison(span)
            scale = self.scale_of(t.of, span)
        elif t.is_complex:
            # C REQUIRES A REAL OPERAND, 6.5.2.4p1. gcc extends `++` to
            # complex by adding one to the real part; that is a GNU
            # extension, it is not what a reader expects, and `z += 1`
            # says it in a way every compiler agrees about.
            self.error("E1414", f"cannot {what} {C.spell(t)}", span,
                       note="C requires a real or pointer operand here",
                       help=f"write `z {'+' if op == '++' else '-'}= 1`")
            return self.poison(span)
        elif t.is_arithmetic:
            scale = 1
        else:
            self.error("E1414",
                       f"cannot {what} {C.spell(t)}", span,
                       note="only arithmetic types and pointers")
            return self.poison(span)
        # `++x` KEEPS ITS OWN NODE rather than becoming `x += 1`. The two
        # differ where it matters: `x += 1` on a `char` computes in `int` and
        # converts back, while `++x` is defined as adding one to the object
        # itself. Lowering reads `scale`, which is 1 for arithmetic and the
        # element size for a pointer, and does neither conversion.
        node = S.Unary(span, t.unqualified(), False, op, operand, postfix)
        node.scale = scale
        return node

    def build_binary(self, op: str, left: S.Expr, right: S.Expr,
                     span: Span) -> S.Expr:
        a = self.lvalue_conversion(left)
        b = self.lvalue_conversion(right)
        if op in ("&&", "||"):
            for e in (a, b):
                if not e.type.is_scalar:
                    self.error("E1415",
                               f"`{op}` needs scalar operands, not "
                               f"{C.spell(e.type)}", e.span)
                    return self.poison(span)
            return S.Logical(span, C.INT, False, op, a, b)
        if op in ("+", "-"):
            return self._additive(op, a, b, span)
        if op in ("*", "/"):
            if not (a.type.is_arithmetic and b.type.is_arithmetic):
                return self._operand_error(op, a, b, span, "arithmetic")
            t = C.usual_arithmetic(a.type, b.type)
            return S.Binary(span, t, False, op,
                            self.convert(a, t, "usual arithmetic"),
                            self.convert(b, t, "usual arithmetic"))
        if op == "%":
            if not (a.type.is_integer and b.type.is_integer):
                return self._operand_error(op, a, b, span, "integer")
            t = C.usual_arithmetic(a.type, b.type)
            return S.Binary(span, t, False, op,
                            self.convert(a, t, "usual arithmetic"),
                            self.convert(b, t, "usual arithmetic"))
        if op in ("&", "|", "^"):
            if not (a.type.is_integer and b.type.is_integer):
                return self._operand_error(op, a, b, span, "integer")
            t = C.usual_arithmetic(a.type, b.type)
            return S.Binary(span, t, False, op,
                            self.convert(a, t, "usual arithmetic"),
                            self.convert(b, t, "usual arithmetic"))
        if op in ("<<", ">>"):
            if not (a.type.is_integer and b.type.is_integer):
                return self._operand_error(op, a, b, span, "integer")
            # THE OPERANDS ARE PROMOTED SEPARATELY and the result is the type
            # of the LEFT one. `1u << 1L` is `unsigned`, not `long` -- the
            # usual arithmetic conversions do not apply to a shift, and using
            # them here silently widens a mask.
            t = C.promote(a.type)
            return S.Binary(span, t, False, op,
                            self.convert(a, t, "promotion"),
                            self.convert(b, C.promote(b.type), "promotion"))
        if op in ("<", ">", "<=", ">="):
            return self._relational(op, a, b, span)
        if op in ("==", "!="):
            return self._equality(op, a, b, span)
        raise AssertionError(op)

    def _operand_error(self, op: str, a: S.Expr, b: S.Expr, span: Span,
                       want: str) -> S.Expr:
        bad = a if not _fits(a.type, want) else b
        self.error("E1416",
                   f"`{op}` needs {want} operands; this one is "
                   f"{C.spell(bad.type)}", bad.span,
                   also=(span, f"in this `{op}`") if bad.span is not span else None)
        return self.poison(span)

    def _additive(self, op: str, a: S.Expr, b: S.Expr, span: Span) -> S.Expr:
        if a.type.is_arithmetic and b.type.is_arithmetic:
            t = C.usual_arithmetic(a.type, b.type)
            return S.Binary(span, t, False, op,
                            self.convert(a, t, "usual arithmetic"),
                            self.convert(b, t, "usual arithmetic"))
        if op == "+" and b.type.is_pointer and a.type.is_integer:
            a, b = b, a             # `3 + p` is `p + 3`
        if a.type.is_pointer and b.type.is_integer:
            if not self._pointee_sized(a, span, op):
                return self.poison(span)
            return S.Binary(span, a.type, False, op, a,
                            self.convert(b, C.PTRDIFF_T, "index"),
                            self.scale_of(a.type.of, span))
        if op == "-" and a.type.is_pointer and b.type.is_pointer:
            if not C.compatible(a.type.of.unqualified(), b.type.of.unqualified()):
                self.error("E1417",
                           f"cannot subtract {C.spell(b.type)} from "
                           f"{C.spell(a.type)}", span,
                           note="the pointed-to types are not compatible")
                return self.poison(span)
            if not self._pointee_sized(a, span, op):
                return self.poison(span)
            return S.Binary(span, C.PTRDIFF_T, False, "-p", a, b,
                            self.scale_of(a.type.of, span))
        self.error("E1418",
                   f"cannot apply `{op}` to {C.spell(a.type)} and "
                   f"{C.spell(b.type)}", span)
        return self.poison(span)

    def _pointee_sized(self, e: S.Expr, span: Span, op: str) -> bool:
        target = e.type.of
        if target.is_function:
            self.error("E1419",
                       f"cannot do arithmetic on {C.spell(e.type)}", span,
                       note="a function has no size")
            return False
        if not target.complete:
            self.error("E1419",
                       f"cannot do arithmetic on a pointer to the incomplete "
                       f"type {C.spell(target)}", span)
            return False
        return True

    def scale_of(self, ty: CType, span: Span):
        """The element size to multiply an index by: a number, or code.

        A VLA HAS A SIZE AND NOT A `sizeof`. `int m[n][n]` as a parameter is
        `int (*m)[n]`, so `m[i]` steps by `n * sizeof(int)` -- which is not
        known until the program runs. Asking `CType.size` for it raises, and
        that exception reached the user as a traceback before this existed.
        """
        return self.vla_size(ty, span) if ty.is_vla else ty.size

    def vla_size(self, ty: CType, span: Span) -> S.Expr:
        """The run-time byte count of a variable-length array."""
        elem = ty.of
        inner = (self.vla_size(elem, span) if elem.is_vla
                 else S.IntLit(span, C.SIZE_T, False, elem.size))
        count = self.convert(ty.vla, C.SIZE_T, "array size")
        return S.Binary(span, C.SIZE_T, False, "*", count, inner)

    def _relational(self, op: str, a: S.Expr, b: S.Expr, span: Span) -> S.Expr:
        # A COMPLEX VALUE IS NOT ORDERED, and C says so by requiring REAL
        # operands here where `==` takes any arithmetic ones. There is no
        # sensible answer for `z < w` and inventing one -- comparing the
        # real parts, say -- would compile a program that means nothing.
        for e in (a, b):
            if e.type.is_complex:
                self.error("E1421",
                           f"`{op}` needs real operands; {C.spell(e.type)} "
                           f"is not ordered", e.span,
                           note="use `creal` or `cabs` to compare magnitudes")
                return self.poison(span)
        if a.type.is_arithmetic and b.type.is_arithmetic:
            t = C.usual_arithmetic(a.type, b.type)
            self._sign_compare_warning(op, a, b, t, span)
            return S.Binary(span, C.INT, False, op,
                            self.convert(a, t, "usual arithmetic"),
                            self.convert(b, t, "usual arithmetic"))
        if a.type.is_pointer and b.type.is_pointer:
            if not C.compatible(a.type.of.unqualified(), b.type.of.unqualified()) \
                    and not (a.type.of.is_void or b.type.of.is_void):
                self.warn("W1420",
                          f"comparing {C.spell(a.type)} with {C.spell(b.type)}",
                          span, note="the pointed-to types are not compatible")
            return S.Binary(span, C.INT, False, op, a,
                            self.convert(b, a.type, "comparison"))
        self.error("E1421",
                   f"cannot compare {C.spell(a.type)} with {C.spell(b.type)}",
                   span)
        return self.poison(span)

    def _sign_compare_warning(self, op: str, a: S.Expr, b: S.Expr, t: CType,
                              span: Span) -> None:
        """`i < len` where one is signed and the other is not.

        The single most common real bug C's conversions cause, and it is
        invisible in the source: the signed side becomes unsigned, so a
        negative value compares GREATER than a positive one. Only reported
        when the signed side could actually be negative, so `sizeof(x) > 0`
        and comparisons against a literal stay quiet.
        """
        if not t.is_integer or t.signed:
            return
        for side, other in ((a, b), (b, a)):
            if side.type.is_integer and side.type.signed and \
                    side.type.size <= t.size and not isinstance(side, S.IntLit):
                self.warn(
                    "W1422",
                    f"comparison between {C.spell(side.type)} and "
                    f"{C.spell(other.type)} converts the signed operand",
                    span,
                    note=f"both sides become {C.spell(t)}, so a negative "
                         f"value compares as a very large one",
                    help="cast the unsigned side, or use a signed type for "
                         "both")
                return

    def _equality(self, op: str, a: S.Expr, b: S.Expr, span: Span) -> S.Expr:
        if a.type.is_arithmetic and b.type.is_arithmetic:
            t = C.usual_arithmetic(a.type, b.type)
            self._sign_compare_warning(op, a, b, t, span)
            return S.Binary(span, C.INT, False, op,
                            self.convert(a, t, "usual arithmetic"),
                            self.convert(b, t, "usual arithmetic"))
        for x, y in ((a, b), (b, a)):
            if x.type.is_pointer and is_null_constant(y):
                return S.Binary(span, C.INT, False, op, x,
                                self.convert(y, x.type, "comparison"))
        if a.type.is_pointer and b.type.is_pointer:
            if not (a.type.of.unqualified().is_void
                    or b.type.of.unqualified().is_void
                    or C.compatible(a.type.of.unqualified(),
                                    b.type.of.unqualified())):
                self.warn("W1420",
                          f"comparing {C.spell(a.type)} with {C.spell(b.type)}",
                          span, note="the pointed-to types are not compatible")
            return S.Binary(span, C.INT, False, op, a,
                            self.convert(b, a.type, "comparison"))
        self.error("E1421",
                   f"cannot compare {C.spell(a.type)} with {C.spell(b.type)}",
                   span)
        return self.poison(span)

    def build_conditional(self, cond: S.Expr, then: S.Expr, other: S.Expr,
                          span: Span) -> S.Expr:
        cond = self.lvalue_conversion(cond)
        if not cond.type.is_scalar:
            self.error("E1423",
                       f"`?:` needs a scalar condition, not {C.spell(cond.type)}",
                       cond.span)
            return self.poison(span)
        a = self.lvalue_conversion(then)
        b = self.lvalue_conversion(other)
        if a.type.is_arithmetic and b.type.is_arithmetic:
            t = C.usual_arithmetic(a.type, b.type)
            return S.Conditional(span, t, False, cond,
                                 self.convert(a, t, "conditional"),
                                 self.convert(b, t, "conditional"))
        if a.type.is_void and b.type.is_void:
            return S.Conditional(span, C.VOID, False, cond, a, b)
        if a.type.is_record and b.type.is_record and a.type.tag is b.type.tag:
            return S.Conditional(span, a.type.unqualified(), False, cond, a, b)
        for x, y, flip in ((a, b, False), (b, a, True)):
            if x.type.is_pointer and is_null_constant(y):
                t = x.type
                out = (self.convert(a, t, "conditional"),
                       self.convert(b, t, "conditional"))
                return S.Conditional(span, t, False, cond, *out)
        if a.type.is_pointer and b.type.is_pointer:
            quals = a.type.of.qual | b.type.of.qual
            if a.type.of.unqualified().is_void or b.type.of.unqualified().is_void:
                t = C.pointer_to(C.VOID.qualified(quals))
            elif C.compatible(a.type.of.unqualified(), b.type.of.unqualified()):
                t = C.pointer_to(C.composite(a.type.of, b.type.of)
                                 .qualified(quals))
            else:
                self.warn("W1420",
                          f"`?:` between {C.spell(a.type)} and "
                          f"{C.spell(b.type)}", span,
                          note="the pointed-to types are not compatible")
                t = a.type
            return S.Conditional(span, t, False, cond,
                                 self.convert(a, t, "conditional"),
                                 self.convert(b, t, "conditional"))
        self.error("E1424",
                   f"`?:` cannot combine {C.spell(a.type)} and "
                   f"{C.spell(b.type)}", span)
        return self.poison(span)

    def build_assign(self, op: str, target: S.Expr, value: S.Expr,
                     span: Span) -> S.Expr:
        if not self.modifiable(target, "assign to"):
            return self.poison(span)
        t = target.type.unqualified()
        if op == "=":
            converted = self.assignable(t, value, "assignment", span)
            if converted is None:
                return self.poison(span)
            return S.Assign(span, t, False, "=", target, converted)
        binop = op[:-1]
        # THE OPERATION HAPPENS IN THE PROMOTED TYPE and the result is
        # converted back. `char c = 200; c += 100;` computes 300 in `int` and
        # stores the low byte -- doing it in `char` would be a different
        # program, and an 8-bit add is not what any machine does anyway.
        probe = self.build_binary(binop, target, value, span)
        if isinstance(probe, S.IntLit):
            return self.poison(span)        # build_binary already reported
        compute = probe.type
        scale = getattr(probe, "scale", 1)
        if t.is_pointer:
            if binop not in ("+", "-"):
                self.error("E1425",
                           f"`{op}` cannot be applied to {C.spell(t)}", span)
                return self.poison(span)
            rhs = self.convert(self.lvalue_conversion(value), C.PTRDIFF_T,
                               "index")
            return S.Assign(span, t, False, op, target, rhs, scale, t)
        rhs = self.lvalue_conversion(value)
        if binop in ("<<", ">>"):
            rhs = self.convert(rhs, C.promote(rhs.type), "promotion")
        else:
            rhs = self.convert(rhs, compute, "usual arithmetic")
        return S.Assign(span, t, False, op, target, rhs, 1, compute)

    def build_index(self, base: S.Expr, index: S.Expr, span: Span) -> S.Expr:
        a = self.lvalue_conversion(base)
        b = self.lvalue_conversion(index)
        if b.type.is_pointer and a.type.is_integer:
            a, b = b, a             # `4[a]`, which C really does allow
        if not a.type.is_pointer:
            self.error("E1426",
                       f"cannot subscript {C.spell(base.type)}", span,
                       note="only an array or a pointer can be subscripted")
            return self.poison(span)
        if not b.type.is_integer:
            self.error("E1427",
                       f"a subscript must be an integer, not {C.spell(b.type)}",
                       index.span)
            return self.poison(span)
        if not self._pointee_sized(a, span, "[]"):
            return self.poison(span)
        return S.Index(span, a.type.of, True, a,
                       self.convert(b, C.PTRDIFF_T, "index"),
                       self.scale_of(a.type.of, span))

    def build_member(self, base: S.Expr, name: str, arrow: bool,
                     span: Span) -> S.Expr:
        owner = base
        if arrow:
            owner = self.lvalue_conversion(base)
            if not owner.type.is_pointer:
                self.error("E1428",
                           f"`->` needs a pointer, not {C.spell(owner.type)}",
                           span,
                           help="use `.` for a struct or union value")
                return self.poison(span)
            record = owner.type.of
        else:
            record = owner.type
            if record.is_pointer:
                self.error("E1429",
                           f"`.` on {C.spell(record)}; did you mean `->`?",
                           span)
                return self.poison(span)
        if not record.is_record:
            self.error("E1430",
                       f"{C.spell(record)} has no members", span)
            return self.poison(span)
        if not record.complete:
            self.error("E1431",
                       f"{C.spell(record)} is incomplete here", span,
                       note="its definition has not been seen yet")
            return self.poison(span)
        path = C.find_member(record, name)
        if path is None:
            near = _nearest(name, [m.name for m in record.tag.members if m.name])
            self.error("E1432",
                       f"{C.spell(record)} has no member named {name!r}", span,
                       help=f"did you mean {near!r}?" if near else "")
            return self.poison(span)
        member = path[-1]
        # THE QUALIFIERS OF THE WHOLE REACH THE PART. Reading `p->x` through a
        # `const struct s *` gives a `const int`, and without this the member
        # is assignable through a pointer that promised not to.
        quals = record.qual | member.type.qual
        ty = member.type.qualified(quals) if quals else member.type
        return S.MemberAccess(span, ty, True, owner, name, path, arrow)

    def build_cast(self, target: CType, operand: S.Expr, span: Span) -> S.Expr:
        e = self.lvalue_conversion(operand)
        if target.is_void:
            return S.Cast(span, C.VOID, False, e)
        if not target.is_scalar:
            self.error("E1433",
                       f"cannot cast to {C.spell(target)}", span,
                       note="a cast target must be a scalar type or `void`")
            return self.poison(span)
        if not e.type.is_scalar:
            self.error("E1434",
                       f"cannot cast {C.spell(e.type)} to {C.spell(target)}",
                       span,
                       note="only a scalar value can be cast")
            return self.poison(span)
        if target.is_pointer and (e.type.is_float or e.type.is_complex) or \
                (target.is_float or target.is_complex) and e.type.is_pointer:
            self.error("E1435",
                       f"cannot cast {C.spell(e.type)} to {C.spell(target)}",
                       span,
                       note="a pointer and a floating type have no conversion")
            return self.poison(span)
        return S.Cast(span, target.unqualified(), False, e)

    def build_call(self, func: S.Expr, args: list[S.Expr],
                   span: Span) -> S.Expr:
        f = self.decay(func)
        if not (f.type.is_pointer and f.type.of.is_function):
            self.error("E1436",
                       f"cannot call {C.spell(func.type)}", span,
                       note="only a function or a pointer to one is callable")
            return self.poison(span)
        sig = f.type.of
        named: list[S.Expr] = []
        varargs: list[S.Expr] = []
        params = sig.params
        if params is None:
            # NO PROTOTYPE: `int f();`. Every argument gets the default
            # argument promotions and nothing is checked, which is what C says
            # and is why a prototype is worth having.
            varargs = [self._default_promote(a) for a in args]
            return S.Call(span, sig.ret, False, f, [], varargs)
        if len(args) < len(params) or (len(args) > len(params)
                                       and not sig.variadic):
            what = "at least " if sig.variadic else ""
            name = getattr(func, "name", None)
            who = f"{name!r}" if name else "this call"
            self.error("E1437",
                       f"{who} takes {what}{len(params)} argument(s), "
                       f"{len(args)} given", span)
            return self.poison(span)
        for i, (arg, p) in enumerate(zip(args, params)):
            name = getattr(func, "name", None)
            what = (f"argument {i + 1} of {name!r}" if name
                    else f"argument {i + 1}")
            converted = self.assignable(p.type, arg, what, arg.span)
            named.append(converted if converted is not None else arg)
        varargs = [self._default_promote(a) for a in args[len(params):]]
        return S.Call(span, sig.ret, False, f, named, varargs)

    def _default_promote(self, e: S.Expr) -> S.Expr:
        """The default argument promotions, 6.5.2.2p6: anything narrower than
        `int` becomes `int`, and `float` becomes `double`. A variadic callee
        reads its arguments at those widths and nothing else."""
        e = self.lvalue_conversion(e)
        if e.type.kind is C.K.FLOAT:
            return self.convert(e, C.DOUBLE, "default argument promotion")
        if e.type.is_integer:
            t = C.promote(e.type)
            if t is not e.type:
                return self.convert(e, t, "default argument promotion")
        return e

    def truth(self, e: S.Expr, where: str) -> S.Expr | None:
        """An expression used as a condition. Reports if it is not scalar."""
        e = self.lvalue_conversion(e)
        if not e.type.is_scalar:
            self.error("E1438",
                       f"{where} needs a scalar condition, not "
                       f"{C.spell(e.type)}", e.span)
            return None
        return e


def _fits(ty: CType, want: str) -> bool:
    return ty.is_arithmetic if want == "arithmetic" else ty.is_integer


def _has_const_member(ty: CType) -> bool:
    if ty.tag is None or not ty.tag.complete:
        return False
    for m in ty.tag.members:
        if m.type.is_const or (m.type.is_record and _has_const_member(m.type)):
            return True
    return False


def _nearest(name: str, candidates: list[str]) -> str | None:
    """The closest spelling, for a "did you mean" -- one edit away, at most.

    Deliberately strict. A suggestion that is wrong is worse than none: it
    sends the reader to check a member that has nothing to do with the
    mistake.
    """
    best, best_d = None, 3
    for c in candidates:
        d = _edit_distance(name, c)
        if d < best_d:
            best, best_d = c, d
    return best


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
