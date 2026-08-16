"""The object runtime, for the reference interpreter.

`link/objects.py` is the dynamic runtime as C, linked into every compiled
program. The interpreter cannot link C, so `asmpython run` trapped on
`apy_from_int` -- which is to say it could not run any program the frontend
now compiles dynamically, which is most of them. That breaks the project's
central invariant from the other side: CPython, the C backend and the machine
backends agreed with each other, and the thing that is supposed to ADJUDICATE
between them could not execute the program at all.

This is the same runtime, backed by REAL PYTHON OBJECTS. An `apy_value` is an
integer handle into a table of ordinary Python values, so `apy_add` is `a + b`
on an `int` and a `float` and every Python semantic -- the numeric tower,
lexicographic list ordering, dict insertion order, `nan` being unordered --
comes for free instead of being restated. A second implementation of the C
would be a second thing to get wrong, and the whole reason this file exists is
to be the thing that is right.

WHAT DOES NOT COME FOR FREE, and why each one is here rather than left to
Python:

  * IDENTITY. `apy_is` is pointer equality in C, and the C shares one cell for
    None, True, False and every int in -5..256 -- CPython's own cache range.
    So the handle table interns exactly those and hands out a fresh handle for
    everything else, and `a = 1; b = 1; a is b` answers the same in both.
  * INTEGERS NO LONGER WRAP, and this entry used to say the opposite. Every
    integer result was truncated here the way the C's `int64_t` truncated,
    "because the invariant under test is that this and the compiled program
    agree -- including where they are both, knowingly, not CPython". The C
    grew arbitrary precision, so the truncation became the thing that made
    the two disagree: `2 ** 64` is a 20-digit number in the compiled program
    and was 0 here. Python's own integers are exactly what the C now
    implements, so the agreement costs nothing to keep.
    `_wrap64` survives for `apy_as_int` alone -- see the note there.
  * MESSAGE TEXT. Most of CPython's messages are what the C was built to
    reproduce, so most come free. The handful that do not are listed at
    `_C_DECIDES` below with what the C says and why.
  * FLOAT REPR is Python's `repr`, which is what `py_repr_double` in
    link/runtime.py computes the hard way. That one agrees by construction.

A NULL HANDLE IS NOT A VALUE. In C a failed operation returns NULL and the
frontend is expected to check the error flag before using the result; passing
that NULL to another operation dereferences it and the program dies somewhere
unrelated. Here it raises immediately, naming the operation, because a
reference interpreter that hid a missing check would be worse than useless.

THE ERROR FLAG is sticky and first-writer-wins, exactly as in the C, and for
the same reason: an operation that runs on a value produced by a failure must
still report the ORIGINAL failure.
"""
from __future__ import annotations

import builtins
import math

from . import types as _ir_types

#: Returned by `call` for a name this file does not provide, so the
#: interpreter's own host table keeps its `putchar`/`put_int`/... bindings and
#: its "no host binding" trap for genuinely unknown symbols.
NOT_MINE = object()

#: CPython's small-int cache, which `apy_from_int` reproduces. Not an
#: optimisation in either place -- it is the reason `a is b` is True for 256
#: and False for 257, and a program can see the difference.
_SMALL_LO, _SMALL_HI = -5, 256


def _wrap64(v: int) -> int:
    """An integer as the C's `int64_t` holds it."""
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= (1 << 63) else v


class Exc:
    """An exception VALUE -- what `except ValueError as e` binds.

    Not a Python exception instance: the C carries a type NAME and one
    argument and nothing else, and building a real `ValueError` here would
    give this file behaviour (a class hierarchy, `args`, chaining) the
    compiled program does not have.
    """

    __slots__ = ("name", "arg", "has_arg", "context", "cause", "suppress",
                 "notes", "rendered")

    def __init__(self, name: str, arg, has_arg: bool = True) -> None:
        #: CHAINING. `context` is whatever was being handled when this one was
        #: raised, set implicitly; `cause` is what `raise X from Y` said. They
        #: are separate because `raise ... from None` SUPPRESSES the context
        #: without having a cause, which one field could not express.
        self.context = None
        self.cause = None
        self.suppress = False
        self.notes = None
        #: WHETHER `arg` is the already-formatted message rather than the
        #: object the program raised -- see the C's `rendered`.
        self.rendered = False
        #: WHETHER there was an argument, not whether it is None. `E()` and
        #: `E(None)` both hold None and are different exceptions: `e.args` is
        #: `()` for one and `(None,)` for the other, and `repr` shows `E()`
        #: against `E(None)`. The C carries the same flag for the same reason.
        self.has_arg = has_arg
        self.name = name
        self.arg = arg


#: Every place the C's answer is NOT Python's, with the C's answer. Each entry
#: is enforced at the call site below; this table is the index, so the next
#: divergence has an obvious place to be recorded rather than being discovered
#: as a backend disagreement.
#:
#: `str % x`      -- Python FORMATS (`'a%d' % 7` is 'a7'). The C has no
#:                   printf-style formatting and reports CPython's
#:                   "not all arguments converted during string formatting"
#:                   for every `%` with a str on the left.
#: `(-8) ** 0.5`  -- Python returns a complex. There is no complex kind, so
#:                   the C reports a ValueError; see its `apy_pow`.
#: `list + tuple` -- Python says "can only concatenate list (not "tuple") to
#:                   list". The C falls through to the generic operand text.
#: `tuple[9]`     -- Python says "tuple index out of range". The C says
#:                   "list index out of range" for both sequence kinds.
#: `xs['a']`      -- Python says "list indices must be integers or slices, not
#:                   str". The C omits the slices, which it does not have.
#: `s[i]`         -- indexes BYTES, not characters, in both. The C's own
#:                   docstring records this; `len` is the only place the
#:                   byte/character distinction is currently resolved.
#: `int('9'*30)`  -- the C's `strtoll` SATURATES at int64. Python would give a
#:                   big int. Neither is CPython's answer, and the point of
#:                   this file is that both paths give the SAME answer.
_C_DECIDES = "see the docstring above and the call sites marked `C DECIDES`"


class ObjectHost:
    """`apy_*` for the interpreter, over a handle table of Python objects."""

    def __init__(self, interp) -> None:
        self._interp = interp
        # Index 0 is never handed out: a 0 handle is the C's NULL, which means
        # "an error was set" and never a value.
        self._cells: list = [None]
        #: id(object) -> its handle, for the kinds `is` asks about. See
        #: `_value`: the C compares pointers, so one object must be one
        #: handle or the two paths disagree about identity.
        self._identity: dict = {}
        self._small: dict[int, int] = {}
        #: `type(x)` results, interned by kind name -- see `_type_of`.
        self._types: dict = {}
        #: `class MyError(ValueError):` -> its base name. See
        #: `_apy_exc_register`.
        self.user_exc: dict = {}
        self._none = self._new(None)
        #: `...`, as one cell -- see `apy_ellipsis`.
        self._ellipsis = self._new(Ellipsis)
        #: The exhaustion sentinel -- see `apy_stop`.
        self._stop = self._new(_STOP)
        self._true = self._new(True)
        self._false = self._new(False)
        self.err: tuple[str, str] | None = None
        #: The exception OBJECT, when the pending error came from a `raise`
        #: rather than an operation failing. None for the latter, which never
        #: had one -- see `_fail_raised`.
        self.err_value = None
        #: THE exception being handled right now, for implicit chaining: a
        #: `raise` inside an `except` records it as the new exception's
        #: `__context__`. One slot rather than a stack, matching the C.
        self.handling = None

    # ── the handle table ────────────────────────────────────────────────────
    def _new(self, obj) -> int:
        self._cells.append(obj)
        return len(self._cells) - 1

    def _get(self, h, where: str):
        h = int(h)
        if h == 0:
            raise _Trap(
                f"{where}: operand is a null value -- an earlier operation "
                f"failed and its result was used without checking "
                f"apy_error_occurred()")
        try:
            return self._cells[h]
        except IndexError:
            raise _Trap(f"{where}: {h} is not a runtime value handle") from None

    def _int(self, v: int) -> int:
        """A handle for an int, interned over the small-int range.

        NOT truncated to 64 bits. The C demotes any big that fits an int64 to
        its small-int representation and keeps exactly one representation per
        value, so a Python `int` of any size is the faithful model of what it
        holds -- and the small-int interning below still reproduces the one
        thing about the C's representation that a program can observe.
        """
        if _SMALL_LO <= v <= _SMALL_HI:
            h = self._small.get(v)
            if h is None:
                h = self._small[v] = self._new(v)
            return h
        return self._new(v)

    def _bool(self, b) -> int:
        return self._true if b else self._false

    def _value(self, obj) -> int:
        """A handle for a computed result, interning what the C interns."""
        if obj is None:
            return self._none
        if obj is True or obj is False:
            return self._bool(obj)
        if isinstance(obj, int):
            return self._int(obj)
        if isinstance(obj, (Instance, Class, Exc, Func, Gen, Iterator)):
            # ONE HANDLE PER OBJECT, so `is` answers about the object and not
            # about which handle it came back through. The C compares
            # pointers, so a fresh handle for the same instance made
            # `m.__self__ is obj` False here and True in a compiled program --
            # the paths disagreeing about identity, which is the one thing
            # identity must not depend on.
            got = self._identity.get(id(obj))
            if got is not None and self._cells[got] is obj:
                return got
            made = self._new(obj)
            self._identity[id(obj)] = made
            return made
        return self._new(obj)

    # ── the error flag ──────────────────────────────────────────────────────
    def _fail(self, kind: str, msg: str) -> int:
        # First writer wins, as `apy_fail` does. A second failure downstream of
        # the first must not overwrite the report of what actually went wrong.
        if self.err is None:
            self.err = (kind, msg)
            self.err_value = None
        return 0

    def _fail_raised(self, exc) -> int:
        """An explicit `raise`, which REPLACES whatever was pending and keeps
        the exception OBJECT.

        Two differences from `_fail`, both matching the C:

        * a `raise` overrides a pending error rather than losing to it --
          `try: raise A / finally: raise B` propagates B;
        * the object survives, so `except E as e` binds what was raised and
          `e.args[0]` is the value rather than the text it rendered to.
          Rebuilding from the message made `raise E(42)` catchable as
          `E('42')`, a different value of a different type.
        """
        self.err = (exc.name, "" if not exc.has_arg
                    else self._text(exc.arg, False))
        self.err_value = exc
        return 0

    def _fail_like(self, exc: BaseException) -> int:
        """Report a Python exception as the runtime would report it.

        The message is Python's own wherever the C reproduces it, which is
        nearly everywhere -- the C's texts were written against CPython's and
        differentially tested against them.
        """
        return self._fail(type(exc).__name__, str(exc))

    # ── kind names ──────────────────────────────────────────────────────────
    @staticmethod
    def kind_name(v) -> str:
        if v is None:
            return "NoneType"
        if v is True or v is False:
            return "bool"
        if isinstance(v, Exc):
            return v.name
        if isinstance(v, Instance):
            # An instance answers with its CLASS's name, which is what makes
            # every TypeError about a user object name the user's type.
            return v.cls.name
        if isinstance(v, Class):
            return "type"
        if isinstance(v, Func):
            return "function"
        if isinstance(v, Cell):
            return "cell"
        if isinstance(v, Super):
            return "super"
        if isinstance(v, Gen):
            return "generator"
        if isinstance(v, Iterator):
            # A CURSOR names what MADE it: `map(str, xs)` is a `map`, which is
            # what `type(...).__name__` answers and what tells a reader why it
            # is lazy. A plain `iter(x)` is an `iterator`.
            return {Iterator.MAP: "map", Iterator.FILTER: "filter",
                    Iterator.ENUMERATE: "enumerate",
                    Iterator.ZIP: "zip"}.get(v.mode, "iterator")
        return type(v).__name__

    # ── text ────────────────────────────────────────────────────────────────
    def _text(self, v, quoted: bool) -> str:
        if isinstance(v, Class):
            return f"<class '{v.name}'>"
        if isinstance(v, Func):
            kind = "bound method" if v.bound is not None else "function"
            return f"<{kind} {v.name} at 0x{id(v):x}>"
        if isinstance(v, Instance):
            # `repr()` and `str()` reach `Instance.__repr__`/`__str__`, which
            # dispatch to the user's methods; the str/repr asymmetry lives
            # there so that a container printing its elements gets it too.
            return repr(v) if quoted else str(v)
        if isinstance(v, Exc):
            # `str(e)` is the argument alone, `repr(e)` is `ValueError('x')`.
            if not quoted:
                # `str(KeyError('k'))` is `"'k'"` -- the REPR of the argument.
                # KeyError alone does this, so a missing key whose text is
                # empty is still visible in the report.
                return ("" if not v.has_arg else self._text(
                    v.arg, not v.rendered and v.name == "KeyError"))
            shown = "" if not v.has_arg else self._text(v.arg, True)
            return f"{v.name}({shown})"
        if isinstance(v, (list, tuple, dict)):
            # A container always shows its ELEMENTS with repr, whichever of
            # str/repr was asked of the container -- `print(['a'])` is `['a']`.
            # Python's own repr does exactly that, including the trailing comma
            # in a one-element tuple, so recursion through `_text` would only
            # be a chance to disagree with it.
            return repr(v)
        if isinstance(v, str):
            return repr(v) if quoted else v
        return repr(v)

    # ── calling compiled code ───────────────────────────────────────────────
    def _no_attr(self, obj, name: str) -> int:
        return self._fail("AttributeError",
                          f"'{self.kind_name(obj)}' object has no "
                          f"attribute '{name}'")

    def _type_of(self, v):
        """`type(x)` as a VALUE, interned by name so `type(1) is type(2)`.

        By NAME rather than by Python class, because every exception shares one
        `Exc` class here and names one of thirty types -- interning by class
        would make `type(KeyError('k')) is type(ValueError('v'))` answer True,
        which the C does not.
        """
        if isinstance(v, Instance):
            return v.cls
        key = "type" if isinstance(v, Class) else self.kind_name(v)
        got = self._types.get(key)
        if got is None:
            got = self._types[key] = Class(key, None)
        return got

    def _require_str(self, value, which: str) -> str:
        """What `__str__`/`__repr__` gave back, which must be a str.

        Converting instead would let `def __str__(self): return self` recurse
        until Python's own recursion limit fired, and that traps rather than
        reporting. The C raises here; so does this, with the same words.
        """
        if isinstance(value, str):
            return value
        self._fail("TypeError", f"{which} returned non-string "
                                f"(type {self.kind_name(value)})")
        raise _UserFailed

    def _invoke(self, f, args: list, kwrest=None, bound: bool = False):
        """Call any callable VALUE with Python-object arguments.

        One entry point for every kind, exactly as `apy_call_n` is in the C:
        a call site never has to know whether it holds a class or a function.

        `kwrest` is the `**kw` dict the caller could not place, threaded down
        rather than appended to `args`: a callee with both `*rest` and `**kw`
        packs the surplus positionals FIRST, so a dict sitting in `args` would
        be swallowed into `rest` instead of landing past it.
        """
        if isinstance(f, Class):
            obj = Instance(f, self)
            init = f.find("__init__")
            if isinstance(init, Func):
                self._invoke_obj(init.bind(obj), args, kwrest, bound)
            elif args:
                self._fail("TypeError", f"{f.name}() takes no arguments")
                raise _UserFailed
            return obj
        if isinstance(f, Instance):
            # THROUGH `_invoke_obj`, not `_send`: a callable instance's
            # `__call__` may take `**kw`, and `_send` has no way to carry the
            # keywords the caller could not place -- they were silently lost.
            m = f.cls.find("__call__")
            if not isinstance(m, Func):
                self._fail("TypeError",
                           f"'{f.cls.name}' object is not callable")
                raise _UserFailed
            return self._invoke_obj(m.bind(f), args, kwrest, bound)
        if not isinstance(f, Func):
            self._fail("TypeError",
                       f"'{self.kind_name(f)}' object is not callable")
            raise _UserFailed
        return self._invoke_obj(f, args, kwrest, bound)

    def _invoke_obj(self, f: "Func", args: list, kwrest=None,
                    bound: bool = False):
        """Run one compiled function. THE re-entry into the interpreter.

        `env` is the function object itself and it is passed FIRST, which is
        the calling convention every backend shares -- see the comment on
        `apy_func_new` in link/objects.py. The env is a fresh handle for the
        BOUND method rather than for the underlying function, because the
        receiver travels in the value and the callee reads its cells out of
        whichever object the call came through.
        """
        slots = [f.bound] if f.bound is not None else []
        declared = f.arity - (1 if f.vararg else 0) - (1 if f.kwarg else 0)
        # WHERE POSITIONS STOP. A keyword-only parameter is declared but not
        # reachable by position, so a surplus argument belongs to `*rest` --
        # or is an error -- rather than landing in it.
        #
        # `bound` means the caller ALREADY matched names to slots, so every
        # argument here belongs where it is: re-applying the limit would
        # truncate a list `apy_call_kw` had just completed and then refill the
        # tail from defaults, discarding what the keywords supplied.
        byslot = declared if bound else declared - f.kwonly
        take = len(args)
        if len(slots) + take > byslot:
            take = max(0, byslot - len(slots))
        slots += list(args[:take])
        # A missing trailing argument comes from the default the `def`
        # evaluated -- one object, shared across calls, which is what
        # `aliasing/default-argument-is-shared` measures.
        while len(slots) < declared:
            d = len(slots) - (declared - len(f.defaults))
            if d < 0 or d >= len(f.defaults):
                break
            slots.append(f.defaults[d])
        if f.vararg:
            slots.append(tuple(args[take:]))
        # `**kw` is the LAST parameter and is bound even when empty: `def
        # f(**kw)` called as `f()` gets `{}`, not nothing.
        if f.kwarg:
            slots.append(dict(kwrest) if kwrest else {})
        if len(slots) != f.arity:
            # POSITIONS, not declared slots: a keyword-only parameter cannot
            # be filled by position, so counting it told the caller to pass
            # more positional arguments than the function accepts.
            want = max(0, f.arity - (1 if f.bound is not None else 0)
                       - (1 if f.vararg else 0) - (1 if f.kwarg else 0)
                       - f.kwonly)
            got = len(args)
            self._fail("TypeError",
                       f"{f.name}() takes {want} positional argument"
                       f"{'' if want == 1 else 's'} but {got} "
                       f"{'was' if got == 1 else 'were'} given")
            raise _UserFailed
        fn = self._interp.module.functions[f.code & ~_FUNC_TAG]
        env = self._new(f)
        result = self._interp._call(fn, [env] + [self._value(s) for s in slots])
        if self.err is not None:
            # The callee failed. Its report is already the first one, so there
            # is nothing to add -- only to stop, so that a NULL result never
            # reaches an operator as though it were a value.
            raise _UserFailed
        return self._get(result, f.name)

    # ── dispatch ────────────────────────────────────────────────────────────
    def call(self, name: str, args: list):
        fn = _TABLE.get(name)
        if fn is None:
            return NOT_MINE
        return fn(self, args)


# Imported late so this module can be read without chasing the interpreter's
# own definitions; `Trap` is the interpreter's "the program did something
# undefined" signal and this file raises exactly one kind of it.
from .interpreter import Trap as _Trap, _FUNC_TAG  # noqa: E402


def _user(h, body, fail=0):
    """Run `body`, turning a user method's failure into the C's NULL return.

    Every entry point that can reach compiled code needs this: `_UserFailed`
    means the error flag is already set and the only thing left is to stop.
    A TypeError raised by the bridge itself -- "has no len()" -- is a report
    this file owes, and is converted with the message Python built.
    """
    try:
        return body()
    except _UserFailed:
        return fail
    except (TypeError, ValueError, ZeroDivisionError, OverflowError,
            KeyError, IndexError) as e:
        return h._fail_like(e)


# ── construction ────────────────────────────────────────────────────────────

def _apy_none(h, a):
    return h._none


def _apy_ellipsis(h, a):
    """`...` and the name `Ellipsis` -- one cell, so `is` answers True."""
    return h._ellipsis


def _apy_from_bool(h, a):
    return h._bool(int(a[0]) != 0)


def _apy_from_int(h, a):
    return h._int(int(a[0]))


def _apy_from_float(h, a):
    return h._new(float(a[0]))


def _read_cstr(h, addr: int) -> str:
    buf = h._interp.mem.buf
    end = buf.index(0, addr)
    return bytes(buf[addr:end]).decode("utf-8", "surrogateescape")


def _apy_from_cstr(h, a):
    return h._new(_read_cstr(h, int(a[0])))


def _apy_from_bytes(h, a):
    addr, n = int(a[0]), int(a[1])
    buf = h._interp.mem.buf
    return h._new(bytes(buf[addr:addr + n]).decode("utf-8", "surrogateescape"))


# ── extraction ──────────────────────────────────────────────────────────────
# No kind check, matching the C: the frontend calls these only where it has
# proved the kind, and a check here would hide a compiler bug behind a zero.

def _apy_index(h, a):
    """A VALUE AS AN INDEX, checked -- a slice bound or a `range` argument,
    which came from the program and may be anything, including a user object
    with `__index__`."""
    v = h._get(a[0], "apy_index")
    if _is_int_like(v):
        return _wrap64(int(v))
    if isinstance(v, Instance) and v.cls.find("__index__") is not None:
        try:
            got = v._send("__index__")
        except _UserFailed:
            return 0
        if _is_int_like(got):
            return _wrap64(int(got))
    h._fail("TypeError",
            f"'{h.kind_name(v)}' object cannot be interpreted as an integer")
    return 0


def _apy_as_int(h, a):
    """The MACHINE WORD behind a value, for the frontend's own arithmetic.

    Still truncating, and it is the only thing left that does: this models the
    C reading `v.i` out of the cell, which is a 64-bit field however large the
    integer it came from. The C's own `apy_as_int` has no kind check for the
    same reason, and an integer too big to fit is a frontend bug in both.
    """
    return _wrap64(int(h._get(a[0], "apy_as_int")))


def _apy_as_float(h, a):
    return float(h._get(a[0], "apy_as_float"))


def _apy_as_bool(h, a):
    return 1 if h._get(a[0], "apy_as_bool") else 0


# ── inspection ──────────────────────────────────────────────────────────────

def _apy_type_name(h, a):
    return h._new(h.kind_name(h._get(a[0], "apy_type_name")))


def _apy_truth(h, a):
    v = h._get(a[0], "apy_truth")
    if isinstance(v, Instance):
        return _user(h, lambda: 1 if v else 0, fail=0)
    return 1 if v else 0


def _apy_len(h, a):
    v = h._get(a[0], "apy_len")
    if isinstance(v, Instance):
        return _user(h, lambda: h._int(len(v)))
    if isinstance(v, (list, tuple, dict, str, bytes, set, frozenset)):
        # A str's length is in CHARACTERS -- the one place the C resolves the
        # byte/character distinction, via its `apy_str_chars`.
        return h._int(len(v))
    return h._fail("TypeError",
                   f"object of type '{h.kind_name(v)}' has no len()")


def _apy_raw_len(h, a):
    """The length as a machine word, for the frontend's own loop bounds.

    A str's is in BYTES, not characters, because the C returns the byte count
    here and `apy_getitem` indexes bytes to match. `apy_len` -- the builtin --
    counts characters in both. The two disagreeing is the C's documented
    limitation, and this file reproduces it rather than quietly being right
    where the compiled program is wrong.
    """
    v = h._get(a[0], "apy_raw_len")
    if isinstance(v, Gen):
        # A GENERATOR has no length until it has been run, so asking for one
        # DRAINS it. `apy_key_at` then reads the same list.
        got = _gen_cache(h, v)
        return 0 if got is None else len(got)
    if isinstance(v, Iterator):
        # A PLAIN cursor over a real container knows WHAT REMAINS without
        # walking it. One that transforms as it goes does not -- filtering may
        # drop any number -- so it is DRAINED, which turns it into a plain
        # cursor over what it produced.
        if v.mode == Iterator.PLAIN and not isinstance(v.src, (Gen, Iterator,
                                                               Instance)):
            return max(0, len(_iter_items(v.src)) - v.i)
        got = _drain_cursor(h, v)
        return 0 if got is None else len(got)
    if isinstance(v, str):
        return len(v.encode("utf-8", "surrogateescape"))
    if isinstance(v, (list, tuple, dict, set, frozenset, bytes)):
        return len(v)
    # A user object with `__len__`. Together with `apy_key_at` falling through
    # to `__getitem__`, that is the whole `__len__`/`__getitem__` iteration
    # protocol -- the one the index walk here fits exactly.
    if isinstance(v, Instance) and v.cls.find("__len__") is not None:
        try:
            return int(v._send("__len__"))
        except _UserFailed:
            return 0
    h._fail("TypeError", f"'{h.kind_name(v)}' object is not iterable")
    return 0


def _apy_repr(h, a):
    return _user(h, lambda: h._new(h._text(h._get(a[0], "apy_repr"), True)))


def _apy_str(h, a):
    return _user(h, lambda: h._new(h._text(h._get(a[0], "apy_str"), False)))


def _apy_print_with(h, a):
    """`print(..., sep='-', end='!')`. A separator or terminator of None means
    the default, which is what an omitted one lowers to -- so "not given" and
    "given as None" are the same request."""
    addr, n = int(a[0]), int(a[1])
    sep = h._get(a[2], "apy_print_with")
    end = h._get(a[3], "apy_print_with")
    parts = []
    for i in range(n):
        handle = h._interp.mem.read(addr + i * 8, _PTR)
        parts.append(h._text(h._get(handle, "apy_print_with"), False))
    text = (sep if isinstance(sep, str) else " ").join(parts)
    h._interp._emit(text + (end if isinstance(end, str) else "\n"))
    return None


def _apy_print(h, a):
    """`items` is the ADDRESS of an array of n handles, not a value.

    The IR has no varargs, so the frontend builds the array in a stack slot
    and passes its address -- the same shape the C reads.
    """
    addr, n = int(a[0]), int(a[1])
    parts = []
    for i in range(n):
        handle = h._interp.mem.read(addr + i * 8, _PTR)
        parts.append(h._text(h._get(handle, "apy_print"), False))
    h._interp._emit(" ".join(parts) + "\n")
    return None


# ── sequences ───────────────────────────────────────────────────────────────

def _apy_list_new(h, a):
    return h._new([])


def _apy_tuple_new(h, a):
    return h._new(())


def _apy_seq_push(h, a):
    """Append, for both the list and the tuple builder.

    A tuple literal is `apy_tuple_new` followed by one push per element, and a
    Python tuple has nothing to push onto. So the CELL is replaced with a
    longer tuple each time, which is quadratic in the element count and is the
    right trade here: the handle does not change, so identity survives exactly
    as it does in the C (which mutates one cell in place), and every reader --
    `repr`, `len`, `==`, `hash`, iteration -- sees a REAL tuple with no
    finalisation step that could be forgotten at one call site.

    The first shape of this held a mutable builder and converted it when
    something asked. That is one more state than the C has, and the extra
    state escaped: a tuple pushed into a list was stored still-unconverted and
    printed as the builder's `repr`.
    """
    seq = h._get(a[0], "apy_seq_push")
    item = h._get(a[1], "apy_seq_push")
    if isinstance(seq, tuple):
        h._cells[int(a[0])] = seq + (item,)
        return h._none
    if not isinstance(seq, list):
        return h._fail(
            "AttributeError",
            f"'{h.kind_name(seq)}' object has no attribute 'append'")
    seq.append(item)
    return h._none


def _apy_getitem(h, a):
    seq = h._get(a[0], "apy_getitem")
    index = h._get(a[1], "apy_getitem")
    if isinstance(seq, Instance):
        return _user(h, lambda: h._value(seq[index]))
    if isinstance(seq, dict):
        return _dict_get(h, seq, index)
    if not _is_int_like(index) and isinstance(index, Instance)             and index.cls.find("__index__") is not None:
        # `__index__` -- how a user object BECOMES an index. PEP 357, and a
        # separate dunder from `__int__` for a reason: a float has `__int__`
        # and is still not a valid subscript.
        try:
            got = index._send("__index__")
        except _UserFailed:
            return 0
        if _is_int_like(got):
            index = got
    if not _is_int_like(index):
        # C DECIDES: CPython says "integers or slices"; the C has no slices
        # and says just "integers", so this does too.
        return h._fail(
            "TypeError",
            f"{h.kind_name(seq)} indices must be integers, "
            f"not '{h.kind_name(index)}'")
    i = int(index)
    if isinstance(seq, (list, tuple)):
        if i < 0:
            i += len(seq)
        if not 0 <= i < len(seq):
            # C DECIDES: "list", even for a tuple. CPython says "tuple index
            # out of range" for one.
            return h._fail("IndexError", "list index out of range")
        return h._value(seq[i])
    if isinstance(seq, bytes):
        # An INT, not a one-byte bytes. Slicing gives bytes back and indexing
        # does not, which is the one asymmetry a reader will not expect and
        # the C reproduces in `apy_bytes_getitem`.
        if i < 0:
            i += len(seq)
        if not 0 <= i < len(seq):
            return h._fail("IndexError", "index out of range")
        return h._int(seq[i])
    if isinstance(seq, str):
        # C DECIDES: BYTE indexing. Identical to CPython for ASCII and wrong
        # for anything else, in both paths equally.
        raw = seq.encode("utf-8", "surrogateescape")
        if i < 0:
            i += len(raw)
        if not 0 <= i < len(raw):
            return h._fail("IndexError", "string index out of range")
        return h._new(raw[i:i + 1].decode("utf-8", "surrogateescape"))
    return h._fail("TypeError",
                   f"'{h.kind_name(seq)}' object is not subscriptable")


def _apy_setitem(h, a):
    seq = h._get(a[0], "apy_setitem")
    index = h._get(a[1], "apy_setitem")
    item = h._get(a[2], "apy_setitem")
    if isinstance(seq, Instance):
        def store():
            seq[index] = item
            return h._none
        return _user(h, store)
    if isinstance(seq, dict):
        return _dict_set(h, seq, index, item)
    if not isinstance(seq, list):
        return h._fail(
            "TypeError",
            f"'{h.kind_name(seq)}' object does not support item assignment")
    if not _is_int_like(index):
        return h._fail("TypeError",
                       f"list indices must be integers, "
                       f"not '{h.kind_name(index)}'")
    i = int(index)
    if i < 0:
        i += len(seq)
    if not 0 <= i < len(seq):
        return h._fail("IndexError", "list assignment index out of range")
    seq[i] = item
    return h._none


# ── dict ────────────────────────────────────────────────────────────────────
# A real Python dict, so insertion order, `d[1] is d[True]` key identity and
# order-free equality all come free -- and all three are things the C had to
# be written carefully to get right.

# Hashability is NOT pre-checked here. Python's own dict raises for an
# unhashable key, with 3.14's exact text -- `cannot use 'tuple' as a dict key
# (unhashable type: 'list')`, which names the key's kind and then the
# innermost offender, and which is recursive through tuples. Reproducing that
# would be three rules to keep in step with CPython for no gain; the C has to
# because it has no dict of its own, and this does not.


def _apy_dict_new(h, a):
    return h._new({})


def _dict_set(h, d, key, val):
    try:
        d[key] = val
    except TypeError as e:
        return h._fail_like(e)
    return h._none


def _apy_clear(h, a):
    """`.clear()` -- empties in place and answers None."""
    v = h._get(a[0], "apy_clear")
    if not isinstance(v, (list, dict, set)):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute 'clear'")
    v.clear()
    return h._none


def _apy_copy(h, a):
    """`.copy()` -- SHALLOW, like Python's. A frozenset's copy is itself,
    which is what CPython returns and is safe for the same reason
    `frozenset(f)` is."""
    v = h._get(a[0], "apy_copy")
    if isinstance(v, frozenset):
        return a[0]
    if not isinstance(v, (list, dict, set)):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute 'copy'")
    return h._new(v.copy())


def _apy_hash(h, a):
    """`hash(x)`. THE VALUES ARE NOT CPYTHON'S -- CPython salts str and bytes
    hashes per process, so there is no fixed number to match. What must hold
    is the CONTRACT: equal values hash equally, and this file and the C agree
    with each other because both go through Python's own `hash` on the same
    objects."""
    v = h._get(a[0], "apy_hash")
    if isinstance(v, Instance):
        return _user(h, lambda: h._int(hash(v)))
    try:
        return h._int(hash(v))
    except TypeError as exc:
        return h._fail_like(exc)


def _apy_iadd(h, a):
    """`x += y` -- NOT sugar for `x = x + y`.

    A list EXTENDS ITSELF and hands itself back, so every other name bound to
    it sees the new elements; a tuple has no in-place form and falls through to
    `+`, which builds a new one. That difference is observable from another
    frame, which is why it cannot be a frontend rewrite.
    """
    x = h._get(a[0], "apy_iadd")
    y = h._get(a[1], "apy_iadd")
    if isinstance(x, Instance) and x.cls.find("__iadd__") is not None:
        try:
            got = x._send("__iadd__", y)
        except _UserFailed:
            return 0
        if got is not NotImplemented:
            return h._value(got)
    if isinstance(x, list):
        got = _apy_extend(h, [a[0], a[1]])
        return a[0] if got else 0
    return _TABLE["apy_add"](h, a)


_IOP_DUNDER = {"|": "__ior__", "&": "__iand__", "^": "__ixor__",
               "-": "__isub__", "*": "__imul__"}


def _apy_iop(h, a):
    """`s |= other` and the rest of the in-place set and dict operators."""
    x = h._get(a[0], "apy_iop")
    y = h._get(a[1], "apy_iop")
    op = str(h._get(a[2], "apy_iop"))
    if isinstance(x, Instance):
        name = _IOP_DUNDER[op]
        if x.cls.find(name) is not None:
            try:
                got = x._send(name, y)
            except _UserFailed:
                return 0
            if got is not NotImplemented:
                return h._value(got)
    if isinstance(x, dict) and op == "|":
        got = _apy_update(h, [a[0], a[1]])
        return a[0] if got else 0
    if isinstance(x, set):
        if not isinstance(y, (set, frozenset)):
            return h._binop_error(op, x, y)
        # Computed then copied back, rather than mutated as it goes: the two
        # operands may be the SAME set.
        out = ({"|": x | y, "&": x & y, "^": x ^ y}.get(op)
               if op != "-" else x - y)
        x.clear()
        x |= out
        return a[0]
    sym = {"|": "apy_bitor", "&": "apy_bitand", "^": "apy_bitxor",
           "-": "apy_sub", "*": "apy_mul"}[op]
    return _TABLE[sym](h, [a[0], a[1]])


def _apy_list_insert(h, a):
    """`xs.insert(i, v)`. The index is CLAMPED, not checked -- which is why
    `insert` never raises where `xs[i] = v` does."""
    seq = h._get(a[0], "apy_list_insert")
    where = h._get(a[1], "apy_list_insert")
    if not isinstance(seq, list):
        return h._fail("AttributeError",
                       f"'{h.kind_name(seq)}' object has no attribute 'insert'")
    if isinstance(where, bool) or not isinstance(where, int):
        return h._fail("TypeError", f"'{h.kind_name(where)}' object cannot be "
                                    f"interpreted as an integer")
    seq.insert(where, h._get(a[2], "apy_list_insert"))
    return h._none


def _apy_list_sort(h, a):
    """`xs.sort()` -- IN PLACE and answering None, which is the whole
    difference from `sorted(xs)`."""
    seq = h._get(a[0], "apy_list_sort")
    if not isinstance(seq, list):
        return h._fail("AttributeError",
                       f"'{h.kind_name(seq)}' object has no attribute 'sort'")
    key = h._get(a[1], "apy_list_sort")
    rev = bool(h._get(a[2], "apy_list_sort"))
    try:
        if key is None:
            seq.sort(reverse=rev)
        else:
            return _user(h, lambda: (
                seq.sort(key=lambda v: h._invoke(key, [v]), reverse=rev)
                or h._none))
    except (TypeError, ValueError) as exc:
        return h._fail_like(exc)
    return h._none


def _apy_list_reverse(h, a):
    seq = h._get(a[0], "apy_list_reverse")
    if not isinstance(seq, list):
        return h._fail("AttributeError", f"'{h.kind_name(seq)}' object has no "
                                         f"attribute 'reverse'")
    seq.reverse()
    return h._none


def _apy_setdefault(h, a):
    """Read, and INSERT when missing -- one lookup's worth of difference from
    `d.get(k, v)`, and the difference is the whole point."""
    d = h._get(a[0], "apy_setdefault")
    if not isinstance(d, dict):
        return h._fail("AttributeError", f"'{h.kind_name(d)}' object has no "
                                         f"attribute 'setdefault'")
    key = h._get(a[1], "apy_setdefault")
    if key in d:
        return h._value(d[key])
    return _dict_set(h, d, key, h._get(a[2], "apy_setdefault")) and a[2]         or a[2]


def _apy_format(h, a):
    """`format(v, spec)`, `f"{v:spec}"` and `"{:spec}".format(v)` -- one
    function, because they are one mini-language.

    Python's own `format` IS the language, so this file uses it and the C
    reimplements it; the two are checked against each other by the differential
    fuzzer and by the integration corpus, which is the only way a hand-written
    parser of a spec stays honest.
    """
    v = h._get(a[0], "apy_format")
    spec = h._get(a[1], "apy_format")
    # A user object formats ITSELF, and is asked BEFORE the empty-spec
    # shortcut: `f"{obj}"` is `format(obj, "")`, which calls `__format__("")`
    # and not `str(obj)`. A class defining both can tell the difference.
    if isinstance(v, Instance):
        try:
            got = v._send("__format__", spec)
        except _UserFailed:
            return 0
        if h.err is not None:
            return 0
        if got is not NotImplemented:
            return h._value(got)
        return h._new(format(h._text(v, False), spec))
    if not spec:
        return h._new(h._text(v, False))
    if isinstance(v, Exc):
        return h._new(format(h._text(v, False), spec))
    try:
        return h._new(format(v, spec))
    except (TypeError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_str_format(h, a):
    """`"{} {:>5} {name!r}".format(...)` -- the replacement-field syntax around
    the spec `apy_format` reads."""
    fmt = h._get(a[0], "apy_str_format")
    if not isinstance(fmt, str):
        return h._fail("AttributeError",
                       f"'{h.kind_name(fmt)}' object has no attribute 'format'")
    args = list(h._get(a[1], "apy_str_format"))
    kw = dict(h._get(a[2], "apy_str_format"))
    try:
        return h._new(fmt.format(*args, **kw))
    except (IndexError, KeyError, ValueError, TypeError) as exc:
        return h._fail_like(exc)


def _apy_str_encode(h, a):
    v = h._get(a[0], "apy_str_encode")   # the encoding is ignored
    if not isinstance(v, str):
        return h._fail("AttributeError", f"'{h.kind_name(v)}' object has no "
                                         f"attribute 'encode'")
    return h._new(v.encode("utf-8"))


def _apy_bytes_decode(h, a):
    v = h._get(a[0], "apy_bytes_decode")  # the encoding is ignored
    if not isinstance(v, bytes):
        return h._fail("AttributeError", f"'{h.kind_name(v)}' object has no "
                                         f"attribute 'decode'")
    return h._new(v.decode("utf-8"))


def _apy_bytes_hex(h, a):
    """`b.hex()` and `b.hex(sep)` -- the separator form is what makes a
    fingerprint readable and is the only reason the argument exists."""
    b = h._get(a[0], "apy_bytes_hex")
    sep = h._get(a[1], "apy_bytes_hex")
    if not isinstance(b, bytes):
        return h._fail("AttributeError",
                       f"'{h.kind_name(b)}' object has no attribute 'hex'")
    return h._new(b.hex(sep) if isinstance(sep, str) and len(sep) == 1
                  else b.hex())


def _apy_bytes_fromhex(h, a):
    # The RECEIVER is `a[0]` and ignored: the shape matches the method table's
    # so that `b.fromhex(s)` and `bytes.fromhex(s)` are one implementation.
    text = h._get(a[1], "apy_bytes_fromhex")
    if not isinstance(text, str):
        return h._fail("TypeError", "fromhex() argument must be str")
    try:
        return h._new(bytes.fromhex(text))
    except ValueError as exc:
        return h._fail_like(exc)


def _apy_to_bytes_n(h, a):
    v = h._get(a[0], "apy_to_bytes_n")
    length = h._get(a[1], "apy_to_bytes_n")
    order = h._get(a[2], "apy_to_bytes_n")
    if not _is_int_like(v):
        return h._fail("AttributeError", f"'{h.kind_name(v)}' object has no "
                                         f"attribute 'to_bytes'")
    if not _is_int_like(length):
        return h._fail("TypeError", "to_bytes() length must be an integer")
    try:
        return h._new(int(v).to_bytes(
            int(length), "little" if order == "little" else "big"))
    except (OverflowError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_as_integer_ratio(h, a):
    """The EXACT fraction the double holds. `0.1` is not one tenth, and this
    is the method that says so."""
    v = h._get(a[0], "apy_as_integer_ratio")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        if not isinstance(v, int):
            return h._fail("AttributeError",
                           f"'{h.kind_name(v)}' object has no attribute "
                           f"'as_integer_ratio'")
    try:
        return h._new(v.as_integer_ratio())
    except (OverflowError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_str_expandtabs(h, a):
    s = h._get(a[0], "apy_str_expandtabs")
    width = h._get(a[1], "apy_str_expandtabs")
    if not isinstance(s, str):
        return h._fail("AttributeError", f"'{h.kind_name(s)}' object has no "
                                         f"attribute 'expandtabs'")
    return h._new(s.expandtabs(int(width) if _is_int_like(width) else 8))


# ── math ────────────────────────────────────────────────────────────────────
# `import math`. Python's own module IS the specification, so this file uses
# it and the C reimplements it; the two are checked against each other by the
# integration corpus, which is the only way a hand-written `isqrt` stays
# honest.
#
# The INTEGER-PRESERVING ones are the point: `math.floor(-2.5)` is the int -3,
# not the float -3.0, and a float there would print and compare differently.

def _math_real(h, v, fn):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        h._fail("TypeError", f"must be real number, not {h.kind_name(v)}")
        return None
    return v


def _math1(name, fn, want_int=False):
    def run(h, a):
        v = h._get(a[0], name)
        if want_int:
            if isinstance(v, bool) or not isinstance(v, int):
                if not isinstance(v, float):
                    return h._fail("TypeError",
                                   f"'{h.kind_name(v)}' object cannot be "
                                   f"interpreted as an integer")
        elif _math_real(h, v, name) is None:
            return 0
        try:
            return h._value(fn(v))
        except (ValueError, OverflowError) as exc:
            return h._fail_like(exc)
    return run


def _math2(name, fn, want_int=False):
    def run(h, a):
        x = h._get(a[0], name)
        y = h._get(a[1], name)
        if want_int:
            for v in (x, y):
                if isinstance(v, bool) or not isinstance(v, int):
                    return h._fail("TypeError",
                                   "'float' object cannot be interpreted as "
                                   "an integer")
        else:
            if _math_real(h, x, name) is None or _math_real(h, y, name) is None:
                return 0
        try:
            return h._value(fn(x, y))
        except (ValueError, OverflowError, ZeroDivisionError) as exc:
            return h._fail_like(exc)
    return run


def _apy_math_isclose(h, a):
    """`isclose(a, b, rel_tol=1e-09, abs_tol=0.0)`. The relative tolerance is
    taken against the LARGER magnitude, which is what makes the relation
    symmetric -- a version dividing by one side is not."""
    vals = []
    for i in range(4):
        v = h._get(a[i], "apy_math_isclose")
        if _math_real(h, v, "isclose") is None:
            return 0
        vals.append(v)
    try:
        return h._bool(math.isclose(vals[0], vals[1], rel_tol=vals[2],
                                    abs_tol=vals[3]))
    except ValueError as exc:
        return h._fail_like(exc)


def _apy_is_integer(h, a):
    v = h._get(a[0], "apy_is_integer")
    if isinstance(v, bool) or isinstance(v, int):
        return h._bool(True)
    if not isinstance(v, float):
        return h._fail("AttributeError", f"'{h.kind_name(v)}' object has no "
                                         f"attribute 'is_integer'")
    return h._bool(v.is_integer())


def _apy_conjugate(h, a):
    v = h._get(a[0], "apy_conjugate")
    if not isinstance(v, (int, float, complex)):
        return h._fail("AttributeError", f"'{h.kind_name(v)}' object has no "
                                         f"attribute 'conjugate'")
    return h._value(v.conjugate())


def _apy_update(h, a):
    """`d.update(other)`, and the `**d` of a call.

    NOT ONLY A MAPPING: `d.update([(1, 2)])` is legal, and so is any iterable
    of two-element iterables -- which is why a list of two-character strings
    works and a list of characters does not.
    """
    target = h._get(a[0], "apy_update")
    src = h._get(a[1], "apy_update")
    if not isinstance(target, dict):
        return h._fail("TypeError",
                       f"'{h.kind_name(target)}' object has no attribute "
                       f"'update'")
    if isinstance(src, dict):
        for k, v in src.items():
            if _dict_set(h, target, k, v) == 0:
                return 0
        return h._none
    items = _seq_items(h, src, "apy_update")
    if items is None:
        return 0
    for pair in items:
        got = list(pair) if isinstance(pair, (list, tuple, str)) else None
        if got is None or len(got) != 2:
            return h._fail("ValueError",
                           "dictionary update sequence element has length "
                           f"{len(got) if got is not None else 1}; 2 is "
                           "required")
        if _dict_set(h, target, got[0], got[1]) == 0:
            return 0
    return h._none


def _apy_dict_set(h, a):
    d = h._get(a[0], "apy_dict_set")
    key = h._get(a[1], "apy_dict_set")
    val = h._get(a[2], "apy_dict_set")
    if not isinstance(d, dict):
        return h._fail(
            "TypeError",
            f"'{h.kind_name(d)}' object does not support item assignment")
    return _dict_set(h, d, key, val)


def _dict_get(h, d, key):
    try:
        return h._value(d[key])
    except KeyError:
        # KeyError's message is the repr of the key, which is what the C
        # builds too -- and what CPython prints for an uncaught one.
        return h._fail("KeyError", h._text(key, True))
    except TypeError as e:
        return h._fail_like(e)


def _apy_key_at(h, a):
    v = h._get(a[0], "apy_key_at")
    i = int(a[1])
    if isinstance(v, Gen):
        got = _gen_cache(h, v)          # what the length query drained
        if got is None:
            return 0
        return h._value(got[i]) if 0 <= i < len(got) else h._none
    if isinstance(v, Iterator):
        # IGNORES `i` AND ADVANCES, exactly as the C's `apy_key_at` does and
        # for the reason documented there: a cursor has a position of its own,
        # and a consumer walking from zero would replay it.
        if v.mode != Iterator.PLAIN or isinstance(v.src, (Gen, Iterator,
                                                          Instance)):
            got = _drain_cursor(h, v)
            if got is None:
                return 0
        items = _iter_items(v.src)
        if v.i >= len(items):
            return h._none
        item = items[v.i]
        v.i += 1
        return h._value(item)
    if isinstance(v, dict):
        # Insertion order, which Python guarantees since 3.7 and the C gets by
        # appending to an association list.
        return h._value(list(v)[i])
    if isinstance(v, (set, frozenset)):
        # A SET IS ITERABLE AND NOT SUBSCRIPTABLE, and this function means
        # "iterate": the C reaches its `v.q` items directly here, so falling
        # through to `apy_getitem` reported `list({1, 2})` as unsubscriptable
        # -- a TypeError on a program CPython runs.
        return h._value(list(v)[i])
    return _apy_getitem(h, [a[0], h._int(i)])


# ── exceptions ──────────────────────────────────────────────────────────────

def _apy_make_exc(h, a):
    name = h._get(a[0], "apy_make_exc")
    arg = h._get(a[1], "apy_make_exc")
    return h._new(Exc(str(name), arg))


def _apy_raise(h, a):
    exc = h._get(a[0], "apy_raise")
    if not isinstance(exc, Exc):
        return h._fail(
            "TypeError",
            f"exceptions must derive from BaseException, "
            f"not '{h.kind_name(exc)}'")
    # The exception being HANDLED becomes this one's `__context__`, unless a
    # `raise ... from` already spoke for the chain. Set here rather than at the
    # `except` because only a raise creates a link.
    # Set even when `from` suppressed it: `__suppress_context__` is whether to
    # PRINT the context, not whether to have one.
    if (exc.context is None
            and h.handling is not None and h.handling is not exc):
        exc.context = h.handling
    # A raise while an error is still PENDING -- `try: raise A finally: raise
    # B` -- chains too, and nothing was "being handled" there: the A is in
    # flight rather than caught.
    if exc.context is None and h.err is not None:
        pending = h.err_value
        if pending is None:
            name, msg = h.err
            pending = Exc(name, msg, has_arg=bool(msg))
            pending.rendered = True
        if pending is not exc:
            exc.context = pending
    return h._fail_raised(exc)


def _apy_exc_register(h, a):
    """A `class MyError(ValueError):` the program wrote.

    Registered into the same hierarchy the builtins use, so `except
    ValueError:` catches it through the code path that already makes `except
    LookupError:` catch a KeyError. See `apy_exc_register` in link/objects.py
    for why this is a NAME and not a type object, and what that costs.
    """
    h.user_exc[str(h._get(a[0], "apy_exc_register"))] = str(
        h._get(a[1], "apy_exc_register"))
    return h._none


def _exc_chain(h, name: str):
    """`name` and every ancestor, builtin or user-defined."""
    seen = []
    while name is not None and name not in seen:
        seen.append(name)
        parent = h.user_exc.get(name)
        if parent is None:
            cls = getattr(builtins, name, None)
            if isinstance(cls, type) and issubclass(cls, BaseException):
                bases = cls.__mro__[1:]
                parent = bases[0].__name__ if bases else None
            else:
                parent = None
        name = parent
    return seen


def _apy_error_handling(h, a):
    """WHAT IS BEING HANDLED right now, for implicit chaining: a `raise`
    inside an `except` records it as the new exception's `__context__`."""
    exc = h._get(a[0], "apy_error_handling")
    was = h.handling
    h.handling = exc if isinstance(exc, Exc) else None
    return h._value(was)


def _apy_add_note(h, a):
    """`e.add_note(text)` -- PEP 678."""
    exc = h._get(a[0], "apy_add_note")
    text = h._get(a[1], "apy_add_note")
    if not isinstance(exc, Exc):
        return h._fail("AttributeError", f"'{h.kind_name(exc)}' object has no "
                                         f"attribute 'add_note'")
    if not isinstance(text, str):
        return h._fail("TypeError", "note must be a str")
    if exc.notes is None:
        exc.notes = []
    exc.notes.append(text)
    return h._none


def _apy_raise_from(h, a):
    """`raise X from Y`. The cause is EXPLICIT, and `from None` suppresses the
    implicit context rather than setting a cause."""
    exc = h._get(a[0], "apy_raise_from")
    cause = h._get(a[1], "apy_raise_from")
    if isinstance(exc, Exc):
        exc.suppress = True
        exc.cause = cause if int(a[2]) and isinstance(cause, Exc) else None
    return _apy_raise(h, [a[0]])


def _apy_error_matches(h, a):
    """Does the current error match a handler named by `a[0]`?

    The hierarchy comes from PYTHON'S OWN builtin exception classes rather
    than from a table -- `except LookupError` catching a KeyError is then a
    fact about the real class tree, not about a list this file keeps in step
    with the C's. The C's `APY_EXC_TREE` is a transcription of that same tree,
    so the two agree by construction on every name in it.

    A name that is not a builtin exception matches only itself, which is the
    C's rule for a name missing from its table and stays right until user
    classes exist and can declare a base.
    """
    want = str(h._get(a[0], "apy_error_matches"))
    if h.err is None:
        return 0
    have = h.err[0]
    if want in h.user_exc or have in h.user_exc:
        # A user-defined name is in neither `builtins` nor Python's class
        # tree, so the walk below cannot see it. The chain does.
        return 1 if want in _exc_chain(h, have) else 0
    want_cls = getattr(builtins, want, None)
    have_cls = getattr(builtins, have, None)
    if (isinstance(want_cls, type) and issubclass(want_cls, BaseException)
            and isinstance(have_cls, type)
            and issubclass(have_cls, BaseException)):
        return 1 if issubclass(have_cls, want_cls) else 0
    return 1 if have == want else 0


def _apy_check_bound(h, a):
    """A LOCAL READ BEFORE IT WAS ASSIGNED.

    Null is the runtime's "no value" and never a legitimate one, so a null
    here means the assignment has not run -- a different thing from having
    been assigned None, which is the distinction `UnboundLocalError` makes.
    """
    if a[0]:
        return a[0]
    return h._fail("UnboundLocalError",
                   f"cannot access local variable "
                   f"'{h._get(a[1], 'apy_check_bound')}' where it is not "
                   f"associated with a value")


def _apy_error_value(h, a):
    if h.err is None:
        return h._none
    # The object that was RAISED, when there was one. An operation that failed
    # never had one, and its message text is all there is to rebuild from.
    if h.err_value is not None:
        return h._new(h.err_value)
    name, msg = h.err
    built = Exc(name, msg, has_arg=bool(msg))
    # The message is ALREADY formatted -- `'k'` for a KeyError from a failed
    # lookup -- so `str(e)` must not repr it a second time.
    built.rendered = True
    return h._new(built)


def _apy_error_occurred(h, a):
    return 1 if h.err is not None else 0


def _apy_error_type(h, a):
    return h._none if h.err is None else h._new(h.err[0])


def _apy_error_message(h, a):
    return h._none if h.err is None else h._new(h.err[1])


def _apy_error_clear(h, a):
    h.err = None
    # The object goes with the flag. Leaving it would let the NEXT error --
    # one from a failing operation, which has no object -- report the previous
    # exception's payload.
    h.err_value = None
    return None


def _apy_fatal_if_error(h, a):
    """Stop the program the way the compiled one stops.

    The C writes `<Type>: <msg>` to STDERR and exits 1 -- deliberately not
    stdout, because the conformance suite diffs stdout and a traceback there
    would turn a correct failure into a wrong answer. Here that is a `Trap`
    carrying the same text: whatever was printed before it has already gone to
    the output stream, so the two paths agree on stdout, which is what is
    compared.
    """
    if h.err is None:
        return None
    kind, msg = h.err
    raise _Trap(f"{kind}: {msg}" if msg else kind)


# ── the numeric tower ───────────────────────────────────────────────────────

def _is_int_like(v) -> bool:
    return isinstance(v, int) and not isinstance(v, float)


def _is_num(v) -> bool:
    return v is not None and isinstance(v, (int, float))


def _result(h, v):
    """A computed result as a handle, truncating an integer to 64 bits.

    Python's integers are unbounded and the C's are not. Where they differ the
    C is the one a compiled program will produce, and the invariant this file
    exists to serve is that the interpreter and that program agree -- so the
    truncation is deliberate, and it is the same truncation `int64_t` performs.
    """
    return h._value(v)


def _binop(name, op, sym):
    """One arithmetic binding: Python's operator, the C's dispatch.

    The kinds the C rejects are rejected here with the C's text, and anything
    it accepts is computed by `op` on the real objects -- so promotion
    (`True + 1` is the int 2, `1 + 1.0` the float 2.0) is Python's rather than
    a restatement of it.
    """
    def run(h, a):
        x = h._get(a[0], name)
        y = h._get(a[1], name)
        bad = _reject(h, sym, x, y)
        if bad is not None:
            return bad
        try:
            return _result(h, op(x, y))
        except _UserFailed:
            return 0
        except TypeError as e:
            if isinstance(x, Instance) or isinstance(y, Instance):
                # Python's own message would name `Instance`, which is this
                # file's class and not the program's type. The C reports the
                # operand pair by kind name, and kind_name answers with the
                # user's class.
                return h._binop_error(sym.split(" ")[0], x, y)
            return h._fail_like(e)
        except (ValueError, ZeroDivisionError, OverflowError) as e:
            return h._fail_like(e)
    return run


def _reject(h, sym: str, x, y):
    """The C's kind rules, for the cases where they are not Python's."""
    if sym == "%" and isinstance(x, str):
        # C DECIDES: `%` on a str is printf-style formatting in Python, and
        # `'a%d' % 7` succeeds there. The runtime has no formatting; it reports
        # CPython's message for the failing form, which is what a program
        # reaching this actually gets.
        return h._fail("TypeError",
                       "not all arguments converted during string formatting")
    if sym == "+" and isinstance(x, str) and not isinstance(y, str):
        return h._fail("TypeError",
                       f'can only concatenate str (not "{h.kind_name(y)}") '
                       f'to str')
    if sym == "+" and isinstance(x, (list, tuple)) and type(x) is not type(y):
        # C DECIDES: the generic operand text, where CPython says
        # "can only concatenate list (not "tuple") to list".
        return h._binop_error(sym, x, y)
    if sym == "*" and (isinstance(x, str) or isinstance(y, str)):
        if not (_is_int_like(x) or _is_int_like(y)):
            other = y if isinstance(x, str) else x
            return h._fail(
                "TypeError",
                f"can't multiply sequence by non-int of type "
                f"'{h.kind_name(other)}'")
    if sym in ("&", "|", "^", "-") and isinstance(x, (set, frozenset)):
        # Set algebra. `&`, `|`, `^` and `-` are the four the C implements on
        # sets, and both operands must BE sets -- Python's operators say the
        # same, unlike its methods, which accept any iterable.
        if not isinstance(y, (set, frozenset)):
            return h._binop_error(sym, x, y)
        return None
    if sym == "|" and isinstance(x, dict) and isinstance(y, dict):
        # `d1 | d2` MERGES, with the right-hand side winning -- PEP 584. Not
        # the bitwise operator despite the spelling.
        return None
    if sym in ("&", "|", "^", "<<", ">>"):
        if not (_is_int_like(x) and _is_int_like(y)):
            return h._binop_error(sym, x, y)
    return None


def _binop_error(h, sym: str, x, y):
    return h._fail(
        "TypeError",
        f"unsupported operand type(s) for {sym}: "
        f"'{h.kind_name(x)}' and '{h.kind_name(y)}'")


ObjectHost._binop_error = _binop_error


def _pow(x, y):
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if x < 0 and isinstance(y, float) and y != math.floor(y):
            # C DECIDES: CPython returns a complex here. There is no complex
            # kind, so the runtime reports -- the one place it knowingly
            # raises where CPython answers.
            raise ValueError("negative number cannot be raised to a "
                             "fractional power (no complex support)")
    return x ** y


def _apy_neg(h, a):
    v = h._get(a[0], "apy_neg")
    if isinstance(v, Instance):
        return _user(h, lambda: h._value(-v))
    # Complex negates both parts. `_is_num` deliberately says no to it -- that
    # predicate gates the ORDERED numeric paths, which complex is not part of.
    if isinstance(v, complex):
        return h._new(-v)
    if not _is_num(v):
        return h._fail("TypeError",
                       f"bad operand type for unary -: '{h.kind_name(v)}'")
    return _result(h, -v)


def _apy_pos(h, a):
    v = h._get(a[0], "apy_pos")
    if isinstance(v, Instance):
        return _user(h, lambda: h._value(+v))
    if isinstance(v, complex):
        return h._new(+v)
    if not _is_num(v):
        return h._fail("TypeError",
                       f"bad operand type for unary +: '{h.kind_name(v)}'")
    return _result(h, +v)


def _apy_invert(h, a):
    v = h._get(a[0], "apy_invert")
    if isinstance(v, Instance):
        return _user(h, lambda: h._value(~v))
    if not _is_int_like(v):
        return h._fail("TypeError",
                       f"bad operand type for unary ~: '{h.kind_name(v)}'")
    # `int(v)` rather than `~v`: `~True` is deprecated on a bool and this is
    # the int operation the C performs on the payload.
    return _result(h, ~int(v))


# ── comparison ──────────────────────────────────────────────────────────────

#: The comparison operators and their dunders, with the REFLECTED name being
#: the MIRRORED operator rather than an `__r`-prefixed one: `a < b` falls back
#: to `b.__gt__(a)`, because what b is asked is the comparison as seen from
#: its side. Comparisons are the one family where that is true.
_CMP_DUNDER = {
    "apy_lt": ("__lt__", "__gt__"), "apy_le": ("__le__", "__ge__"),
    "apy_gt": ("__gt__", "__lt__"), "apy_ge": ("__ge__", "__le__"),
    "apy_eq": ("__eq__", "__eq__"), "apy_ne": ("__ne__", "__ne__"),
}


def _cmpop(name, op):
    def run(h, a):
        x = h._get(a[0], name)
        y = h._get(a[1], name)
        # A USER CLASS ANSWERS WITH WHATEVER ITS DUNDER RETURNS, not with a
        # bool: `__lt__` returning a string is legal and its result is the
        # value of `<`. Coercing it -- which `h._bool` does -- turned every
        # such answer into True.
        if isinstance(x, Instance) or isinstance(y, Instance):
            direct, reflected = _CMP_DUNDER[name]
            for who, other, which in ((x, y, direct), (y, x, reflected)):
                if not isinstance(who, Instance) or who.cls.find(which) is None:
                    continue
                try:
                    got = who._send(which, other)
                except _UserFailed:
                    return 0
                if got is not NotImplemented:
                    return h._value(got)
        try:
            return h._bool(op(x, y))
        except _UserFailed:
            return 0
        except TypeError as e:
            return h._fail_like(e)
    return run


def _apy_is(h, a):
    """Identity, which is HANDLE equality -- not `is` on the Python objects.

    Python interns far more than the runtime does (every string literal, for
    one), so asking `x is y` about the backing objects would answer True where
    a compiled program answers False. The handle is the address.
    """
    h._get(a[0], "apy_is")
    h._get(a[1], "apy_is")
    return h._bool(int(a[0]) == int(a[1]))


def _apy_contains(h, a):
    needle = h._get(a[0], "apy_contains")
    hay = h._get(a[1], "apy_contains")
    if isinstance(hay, Instance) and hay.cls.find("__contains__") is None:
        # No `__contains__`. `in` falls back to ITERATION, which is CPython's
        # rule and the reason a class with only `__getitem__` supports it.
        walked = _apy_iterable(h, [a[1]])
        if not walked:
            return 0
        if walked != a[1]:
            return _apy_contains(h, [a[0], walked])
        # `_apy_iterable` left it alone, which means `__len__` plus
        # `__getitem__`: the index walk IS its protocol, so walk it.
        n = _apy_raw_len(h, [a[1]])
        if h.err is not None:
            return 0
        for i in range(n):
            try:
                item = hay._send("__getitem__", i)
            except _UserFailed:
                return 0
            if h.err is not None:
                return 0
            if item == needle:
                return h._bool(True)
        return h._bool(False)
    try:
        return h._bool(needle in hay)
    except _UserFailed:
        return 0
    except TypeError as e:
        return h._fail_like(e)


# ── conversions ─────────────────────────────────────────────────────────────

def _apy_to_int(h, a):
    v = h._get(a[0], "apy_to_int")
    if not isinstance(v, (int, float, str)):
        return h._fail("TypeError",
                       f"int() argument must be a string, a bytes-like "
                       f"object or a real number, not '{h.kind_name(v)}'")
    try:
        n = int(v)
    except (ValueError, OverflowError) as e:
        return h._fail_like(e)
    # THE SATURATION THAT USED TO BE HERE IS GONE. `int('1' + '0' * 40)` was
    # clamped to INT64_MAX, because the C reached `strtoll` and strtoll
    # saturates -- "neither is CPython's answer, but the two paths have to
    # give the SAME wrong answer". The C parses arbitrary length now and gives
    # CPython's answer, so keeping the clamp would make this the only one of
    # the three still wrong.
    return _result(h, n)


def _apy_to_float(h, a):
    v = h._get(a[0], "apy_to_float")
    if not isinstance(v, (int, float, str)):
        return h._fail("TypeError",
                       f"float() argument must be a string or a real number, "
                       f"not '{h.kind_name(v)}'")
    try:
        return h._new(float(v))
    except ValueError as e:
        return h._fail_like(e)


def _apy_to_bool(h, a):
    return h._bool(bool(h._get(a[0], "apy_to_bool")))


# ── the table ───────────────────────────────────────────────────────────────
# Written out rather than derived from `OBJECT_NAMES`, so a symbol added to
# the C without a binding here is a KeyError naming it rather than a program
# that silently does nothing. `tests/.../test_endtoend.py` asserts the two
# sets are equal.


# ── arbitrary precision integers ────────────────────────────────────────────
# Every one of these is a delegation, because Python's integers are exactly
# what the C's second integer kind implements. The C had to be written; this
# only has to agree with it, and the differential tool checks that the two
# do -- see tools/objects_diff.py.

def _apy_pow3(h, a):
    base = h._get(a[0], "apy_pow3")
    exp = h._get(a[1], "apy_pow3")
    mod = h._get(a[2], "apy_pow3")
    if not all(isinstance(v, int) for v in (base, exp, mod)):
        return h._fail("TypeError",
                       "pow() 3rd argument not allowed unless all arguments "
                       "are integers")
    try:
        return h._int(pow(int(base), int(exp), int(mod)))
    except (ValueError, ZeroDivisionError) as exc:
        return h._fail_like(exc)


def _apy_divmod(h, a):
    x = h._get(a[0], "apy_divmod")
    y = h._get(a[1], "apy_divmod")
    bad = _reject(h, "divmod()", x, y)
    if bad is not None:
        return bad
    try:
        q, r = divmod(x, y)
    except ZeroDivisionError:
        return h._fail("ZeroDivisionError", "division by zero")
    return h._new((h._value(q), h._value(r)))


def _apy_bit_length(h, a):
    v = h._get(a[0], "apy_bit_length")
    if not isinstance(v, int):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute "
                       f"'bit_length'")
    return h._int(int(v).bit_length())


def _apy_bit_count(h, a):
    v = h._get(a[0], "apy_bit_count")
    if not isinstance(v, int):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute "
                       f"'bit_count'")
    return h._int(int(v).bit_count())


def _base_text(h, a, fn, render):
    v = _base_arg(h, h._get(a[0], fn), fn)
    if v is None:
        return 0
    return h._new(render(int(v)))


def _base_arg(h, v, fn):
    """`hex(obj)` goes through `__index__` -- PEP 357 names these three as the
    operations it exists for, alongside subscripting."""
    if isinstance(v, Instance) and v.cls.find("__index__") is not None:
        try:
            got = v._send("__index__")
        except _UserFailed:
            return None
        if _is_int_like(got):
            return got
    if not _is_int_like(v):
        h._fail("TypeError",
                f"{fn}() argument can't be interpreted as an integer")
        return None
    return v


def _apy_bin(h, a):
    return _base_text(h, a, "bin", bin)


def _apy_oct(h, a):
    return _base_text(h, a, "oct", oct)


def _apy_hex(h, a):
    return _base_text(h, a, "hex", hex)


def _apy_to_int_base(h, a):
    """`int(s, base)` for any base from 2 to 36, and base 0 -- which means
    "read the prefix", so `int('0x1f', 0)` is 31 and `int('17', 0)` is 17."""
    v = h._get(a[0], "apy_to_int_base")
    base = h._get(a[1], "apy_to_int_base")
    if not isinstance(v, str):
        return h._fail("TypeError",
                       "int() can't convert non-string with explicit base")
    if isinstance(base, bool) or not isinstance(base, int):
        return h._fail("TypeError",
                       f"'{h.kind_name(base)}' object cannot be interpreted "
                       f"as an integer")
    if base != 0 and not 2 <= base <= 36:
        return h._fail("ValueError", "int() base must be >= 2 and <= 36, or 0")
    try:
        return h._int(int(v, base))
    except ValueError:
        return h._fail("ValueError",
                       f"invalid literal for int() with base {base}: "
                       f"{v!r}")


# ── the object model ────────────────────────────────────────────────────────
# The kinds the C's `apy_obj` union holds that Python has no equivalent for. A
# str is a str and a list is a list -- those are modelled by the real thing,
# which is why `sorted` here IS `sorted` and its stability comes from CPython
# rather than from a restatement of it. These five have no such stand-in.

#: The IR type of a pointer-sized value, for reading an argument array out of
#: interpreter memory. Named here rather than imported into every use so the
#: import stays one line at the top of the file.
_PTR = _ir_types.PTR


class _UserFailed(Exception):
    """A user method reached from inside a runtime entry point failed.

    NOT an error report of its own: the error flag is already set, and the
    only thing left is to stop. Every entry point that can re-enter compiled
    code catches this and answers the C's NULL, so a failed `__eq__` inside a
    `sorted` looks exactly like a failed `apy_add` to the caller.
    """


class Cell:
    """A captured variable's box.

    What a closure captures is the BOX and not the value in it, so two
    closures over one variable see each other's writes. Copying the value at
    capture time passes every test where only one closure exists and fails the
    one where two do.
    """

    __slots__ = ("slot",)

    def __init__(self, initial=None) -> None:
        self.slot = initial


class Class:
    """A user class: its name, its base, and what its body bound.

    SINGLE INHERITANCE, as a chain of `base` pointers -- which is what makes
    the lookup below a walk rather than a linearisation, and what the C's
    `apy_type_is_sub` reproduces exactly.
    """

    __slots__ = ("name", "base", "dict")

    def __init__(self, name: str, base=None) -> None:
        self.name = name
        self.base = base
        self.dict: dict = {}

    def find(self, name: str):
        """The attribute, searching this class and then its bases. None when
        no class in the chain has it -- the caller decides whether that is an
        error, because an instance still has its own dict to try."""
        here = self
        while isinstance(here, Class):
            if name in here.dict:
                return here.dict[name]
            here = here.base
        return None

    def is_sub(self, other) -> bool:
        """Is `other` reachable by base pointers? The `isinstance` rule for
        user classes, and the only thing single inheritance makes cheap."""
        here = self
        while isinstance(here, Class):
            if here is other:
                return True
            here = here.base
        return False


class Instance:
    """A user object: its class, its own attributes, and the host that made it.

    The host reference is what lets a dunder re-enter the interpreter. One
    ObjectHost exists per interpreter, so this is a back-pointer and not a
    second source of truth.
    """

    __slots__ = ("cls", "dict", "h")

    def __init__(self, cls, h) -> None:
        self.cls = cls
        self.dict: dict = {}
        self.h = h

    # -- the dunder bridge --------------------------------------------------
    def _send(self, name: str, *args):
        """Call a method of this object's class, if it has one.

        `NotImplemented` means THE CLASS DOES NOT DEFINE IT, which every
        caller distinguishes from the method having returned None -- a class
        with no `__str__` falls back to `__repr__`, and one whose `__str__`
        returns None is an error.
        """
        m = self.cls.find(name)
        if m is None or not isinstance(m, Func):
            return NotImplemented
        return self.h._invoke_obj(m.bind(self), list(args))

    def __repr__(self):
        out = self._send("__repr__")
        if out is NotImplemented:
            return f"<{self.cls.name} object at 0x{id(self):x}>"
        return self.h._require_str(out, "__repr__")

    def __str__(self):
        out = self._send("__str__")
        if out is NotImplemented:
            return self.__repr__()
        return self.h._require_str(out, "__str__")

    def __bool__(self):
        out = self._send("__bool__")
        if out is NotImplemented:
            out = self._send("__len__")
            # No `__bool__` and no `__len__` is ALWAYS TRUE. Answering False
            # would silently invert every bare `if obj:`.
            return True if out is NotImplemented else bool(out)
        return bool(out)

    def __len__(self):
        out = self._send("__len__")
        if out is NotImplemented:
            raise TypeError(f"object of type '{self.cls.name}' has no len()")
        return int(out)

    def __eq__(self, other):
        out = self._send("__eq__", other)
        if out is NotImplemented:
            return self is other
        return out

    def __ne__(self, other):
        out = self._send("__ne__", other)
        if out is NotImplemented:
            eq = self._send("__eq__", other)
            return self is not other if eq is NotImplemented else not eq
        return out

    def __hash__(self):
        out = self._send("__hash__")
        if out is NotImplemented:
            # A class defining `__eq__` and not `__hash__` is UNHASHABLE in
            # Python, and saying so is what makes a dict key of one an error
            # rather than a silently wrong lookup.
            if self.cls.find("__eq__") is not None:
                raise TypeError(f"unhashable type: '{self.cls.name}'")
            return id(self) >> 3
        return int(out)

    def __getitem__(self, key):
        out = self._send("__getitem__", key)
        if out is NotImplemented:
            raise TypeError(f"'{self.cls.name}' object is not subscriptable")
        return out

    def __setitem__(self, key, value):
        out = self._send("__setitem__", key, value)
        if out is NotImplemented:
            raise TypeError(f"'{self.cls.name}' object does not support item "
                            f"assignment")

    def __contains__(self, needle):
        out = self._send("__contains__", needle)
        if out is NotImplemented:
            raise TypeError(f"argument of type '{self.cls.name}' is not "
                            f"iterable")
        return bool(out)

    def __neg__(self):
        out = self._send("__neg__")
        if out is NotImplemented:
            raise TypeError(f"bad operand type for unary -: "
                            f"'{self.cls.name}'")
        return out

    def __pos__(self):
        out = self._send("__pos__")
        if out is NotImplemented:
            raise TypeError(f"bad operand type for unary +: "
                            f"'{self.cls.name}'")
        return out

    def __invert__(self):
        out = self._send("__invert__")
        if out is NotImplemented:
            raise TypeError(f"bad operand type for unary ~: "
                            f"'{self.cls.name}'")
        return out

    def __abs__(self):
        out = self._send("__abs__")
        if out is NotImplemented:
            raise TypeError(f"bad operand type for abs(): '{self.cls.name}'")
        return out

    def __index__(self):
        out = self._send("__index__")
        if out is NotImplemented:
            raise TypeError(f"'{self.cls.name}' object cannot be interpreted "
                            f"as an integer")
        return int(out)


class Func:
    """A callable: compiled code, its captured boxes, and maybe a receiver.

    Defaults and `*rest` live HERE and not at the call site, exactly as in the
    C: a call through `apy_call` reaches a function whose definition the caller
    never saw, and every method call is one of those.
    """

    __slots__ = ("code", "arity", "name", "cells", "bound", "defaults",
                 "vararg", "pnames", "kwarg", "kwonly", "posonly", "doc")

    def __init__(self, code, arity, name, ncells, ndefaults=0,
                 vararg=False) -> None:
        self.code = code
        self.arity = arity
        self.name = name
        self.cells = [None] * ncells
        self.bound = None
        self.defaults = [None] * ndefaults
        self.vararg = vararg
        #: WHETHER the last declared parameter is `**kw`.
        self.kwarg = False
        #: How many TRAILING declared parameters are keyword-only. A position
        #: cannot reach one, so positional filling stops short of them.
        self.kwonly = 0
        #: How many LEADING declared parameters are positional-only. Their
        #: names ARE recorded -- so a keyword call can be told which mistake
        #: it made -- but the matcher skips them.
        self.posonly = 0
        #: Parameter names in declaration order, including `self`. Only a
        #: KEYWORD argument through a value needs them -- see the C's
        #: `pnames`.
        self.pnames = [None] * arity
        #: The DOCSTRING, or None. Recorded because `f.__doc__` is the one
        #: piece of a `def` a program routinely reads back.
        self.doc = None

    def bind(self, receiver) -> "Func":
        out = Func(self.code, self.arity, self.name, 0)
        # SHARED, not copied: a bound method sees the same boxes and the same
        # default objects as the function it came from.
        out.cells = self.cells
        out.defaults = self.defaults
        out.vararg = self.vararg
        out.kwarg = self.kwarg
        out.kwonly = self.kwonly
        out.posonly = self.posonly
        out.pnames = self.pnames
        out.doc = self.doc
        out.bound = receiver
        return out


class Super:
    """What `super()` evaluates to: where to start looking, and for whom.

    `frm` is the class the calling method was DEFINED IN, not `type(recv)`.
    With `B(A)` and `C(B)`, a `super().m()` written in B's `m` must find A's;
    starting from `type(recv)` would find B's own and recurse until the stack
    ran out.
    """

    __slots__ = ("frm", "recv")

    def __init__(self, frm, recv) -> None:
        self.frm = frm
        self.recv = recv


def _make_binary(name, rname):
    def run(self, other):
        return self._send(name, other)

    def rrun(self, other):
        return self._send(rname, other)
    return run, rrun


for _op, _rop in (("add", "radd"), ("sub", "rsub"), ("mul", "rmul"),
                  ("truediv", "rtruediv"), ("floordiv", "rfloordiv"),
                  ("mod", "rmod"), ("pow", "rpow"), ("and", "rand"),
                  ("or", "ror"), ("xor", "rxor"), ("lshift", "rlshift"),
                  ("rshift", "rrshift")):
    _f, _rf = _make_binary(f"__{_op}__", f"__{_rop}__")
    setattr(Instance, f"__{_op}__", _f)
    setattr(Instance, f"__{_rop}__", _rf)

for _op, _mirror in (("lt", "gt"), ("le", "ge"), ("gt", "lt"), ("ge", "le")):
    # The reflected form of a comparison is the MIRRORED operator, not an
    # `__r`-prefixed one: `a < b` retries as `b.__gt__(a)`. Python's own
    # protocol does that for us as long as both sides are defined here.
    setattr(Instance, f"__{_op}__", _make_binary(f"__{_op}__", f"__{_mirror}__")[0])

_UNARY_SYMBOL = {"__neg__": "-", "__pos__": "+", "__invert__": "~"}

for _op in ("neg", "pos", "invert"):
    def _unary(self, _name=f"__{_op}__"):
        out = self._send(_name)
        if out is NotImplemented:
            raise TypeError(f"bad operand type for unary "
                            f"{_UNARY_SYMBOL[_name]}: '{self.cls.name}'")
        return out
    setattr(Instance, f"__{_op}__", _unary)


# ── the entry points ────────────────────────────────────────────────────────

def _apy_cell_new(h, a):
    return h._new(Cell(h._get(a[0], "apy_cell_new")))


def _apy_cell_get(h, a):
    return h._value(h._get(a[0], "apy_cell_get").slot)


def _apy_cell_set(h, a):
    h._get(a[0], "apy_cell_set").slot = h._get(a[1], "apy_cell_set")
    return h._none


def _apy_func_new(h, a):
    """`a[0]` is a FUNC_ADDR, which is not a value handle.

    The interpreter tags a function address as `_FUNC_TAG | index`, so it can
    never be confused with a handle, and it is stored verbatim -- decoding
    happens once, where the call is made.
    """
    return h._new(Func(int(a[0]), int(a[1]),
                       str(h._get(a[2], "apy_func_new")), int(a[3]),
                       int(a[4]), bool(int(a[5]))))


def _apy_func_default(h, a):
    f = h._get(a[0], "apy_func_default")
    f.defaults[int(a[1])] = h._get(a[2], "apy_func_default")
    return a[0]


def _apy_func_doc(h, a):
    h._get(a[0], "apy_func_doc").doc = str(h._get(a[1], "apy_func_doc"))
    return a[0]


def _apy_func_posonly(h, a):
    h._get(a[0], "apy_func_posonly").posonly = int(a[1])
    return a[0]


def _apy_func_kwonly(h, a):
    h._get(a[0], "apy_func_kwonly").kwonly = int(a[1])
    return a[0]


def _apy_func_kwarg(h, a):
    h._get(a[0], "apy_func_kwarg").kwarg = int(a[1]) != 0
    return a[0]


def _apy_func_param(h, a):
    f = h._get(a[0], "apy_func_param")
    i = int(a[1])
    if 0 <= i < len(f.pnames):
        f.pnames[i] = str(h._get(a[2], "apy_func_param"))
    return a[0]


def _apy_func_cell(h, a):
    f = h._get(a[0], "apy_func_cell")
    f.cells[int(a[1])] = h._get(a[2], "apy_func_cell")
    return a[0]


def _apy_env_cell(h, a):
    env = h._get(a[0], "apy_env_cell")
    if not isinstance(env, Func):
        return h._fail("SystemError", "closure environment is not a function")
    return h._value(env.cells[int(a[1])])


def _apy_type_new(h, a):
    base = h._get(a[1], "apy_type_new")
    return h._new(Class(str(h._get(a[0], "apy_type_new")),
                        base if isinstance(base, Class) else None))


def _apy_type_set(h, a):
    cls = h._get(a[0], "apy_type_set")
    cls.dict[str(h._get(a[1], "apy_type_set"))] = h._get(a[2], "apy_type_set")
    return h._none


def _apy_instance_new(h, a):
    cls = h._get(a[0], "apy_instance_new")
    if not isinstance(cls, Class):
        return h._fail("TypeError",
                       f"'{h.kind_name(cls)}' object is not callable")
    return h._new(Instance(cls, h))


def _apy_type_object(h, a):
    return h._new(h._type_of(h._get(a[0], "apy_type_object")))


def _apy_enter(h, a):
    """`with cm:` -- the `__enter__` half.

    A missing method is reported as "not a context manager" rather than as a
    missing attribute, matching the C, because that is the sentence that tells
    the reader which half of the protocol to write.
    """
    cm = h._get(a[0], "apy_enter")
    if not isinstance(cm, Instance) or cm.cls.find("__enter__") is None:
        return h._fail("TypeError",
                       f"'{h.kind_name(cm)}' object does not support the "
                       f"context manager protocol (missed __enter__ method)")
    return _user(h, lambda: h._value(cm._send("__enter__")))


def _apy_exit(h, a):
    """`__exit__(type, value, tb)` on every path out of a `with`.

    The exception's TYPE is passed, not just the value: a manager that logs
    `et.__name__` needs it, and a true return has to SWALLOW the exception
    rather than merely observe it -- which is why the caller branches on what
    this returns.
    """
    cm = h._get(a[0], "apy_exit")
    exc = h._get(a[1], "apy_exit")
    if not isinstance(cm, Instance) or cm.cls.find("__exit__") is None:
        return h._fail("TypeError",
                       f"'{h.kind_name(cm)}' object does not support the "
                       f"context manager protocol (missed __exit__ method)")
    if isinstance(exc, Exc):
        args = (h._type_of(exc), exc, None)
    else:
        args = (None, None, None)
    return _user(h, lambda: h._value(cm._send("__exit__", *args)))


def _apy_super(h, a):
    frm = h._get(a[0], "apy_super")
    if not isinstance(frm, Class):
        return h._fail("TypeError", "super(type, obj): obj must be an "
                                    "instance or subtype of type")
    return h._new(Super(frm, h._get(a[1], "apy_super")))


def _apy_is_instance(h, a):
    return 1 if isinstance(h._get(a[0], "apy_is_instance"), Instance) else 0


def _apy_getattr(h, a):
    """`x.name`. `__getattribute__` INTERCEPTS EVERYTHING, before the instance
    dict is even looked at -- that is what distinguishes it from
    `__getattr__`, which is consulted only after a miss."""
    obj = h._get(a[0], "apy_getattr")
    if isinstance(obj, Instance)             and obj.cls.find("__getattribute__") is not None:
        return _user(h, lambda: h._value(
            obj._send("__getattribute__", str(h._get(a[1], "apy_getattr")))))
    return _apy_default_getattr(h, a)


def _apy_default_getattr(h, a):
    """The DEFAULT lookup: instance dict, then class, then `__getattr__`.

    Named separately because a class that overrides `__getattribute__` needs a
    way to do what it overrode, and `object.__getattribute__(self, name)` is
    how Python spells that -- the only way out of the recursion.
    """
    obj = h._get(a[0], "apy_getattr")
    name = str(h._get(a[1], "apy_getattr"))
    if isinstance(obj, Instance):
        # The INSTANCE DICT WINS over the class, so `self.x = 1` shadows a
        # class attribute of the same name.
        if name in obj.dict:
            return h._value(obj.dict[name])
        found = obj.cls.find(name)
        if found is not None:
            # A function on the class becomes a BOUND METHOD, and a fresh one
            # per access -- which is what CPython does and what
            # `datamodel/method-objects-are-created-per-access` measures.
            return h._new(found.bind(obj)) if isinstance(found, Func) \
                else h._value(found)
        if name == "__class__":
            return h._value(obj.cls)
        # THE INSTANCE'S OWN attributes, and the real dict rather than a copy:
        # `obj.__dict__["x"] = 1` is how a program sets an attribute
        # dynamically, and a copy would accept the write and lose it.
        if name == "__dict__":
            return h._new(obj.dict)
        # `__getattr__` -- the LAST resort, asked only after the instance dict
        # and the class have both missed. That ordering is the whole protocol.
        if obj.cls.find("__getattr__") is not None:
            return _user(h, lambda: h._value(obj._send("__getattr__", name)))
        return h._no_attr(obj, name)
    if isinstance(obj, Class):
        if name == "__name__":
            return h._new(obj.name)
        # What the class BODY bound, not what it inherited -- the difference
        # `"x" in vars(C)` asks about. A copy: a type's dict is a mapping
        # proxy in CPython and is not writable.
        if name == "__dict__":
            return h._new(dict(obj.dict))
        if name == "__bases__":
            return h._new((obj.base,) if obj.base is not None else ())
        found = obj.find(name)
        # Through the CLASS a method is UNBOUND: `C.m(x)` passes x as self.
        if found is not None:
            return h._value(found)
        return h._fail("AttributeError",
                       f"type object '{obj.name}' has no attribute '{name}'")
    if isinstance(obj, Super):
        base = obj.frm.base
        found = base.find(name) if base is not None else None
        if found is None:
            return h._fail("AttributeError",
                           f"'super' object has no attribute '{name}'")
        return h._new(found.bind(obj.recv)) if isinstance(found, Func) \
            else h._value(found)
    if isinstance(obj, complex):
        # `.real` and `.imag` are floats, not complexes -- `(1+2j).real` is
        # `1.0`. `h._value` would keep whatever Python's attribute gives,
        # which is already a float, so this is just the two names.
        if name in ("real", "imag"):
            return h._new(getattr(obj, name))
        return h._no_attr(obj, name)
    if isinstance(obj, Func):
        if name in ("__name__", "__qualname__"):
            # No qualified name is recorded -- a nested `def` knows its own
            # name and not its enclosing scope's -- so the plain one is what
            # there is.
            return h._new(obj.name)
        # `m.__self__` is the RECEIVER of a bound method, and its absence is
        # how a program tells a bound method from a plain function.
        if name == "__self__":
            if obj.bound is None:
                return h._no_attr(obj, name)
            return h._value(obj.bound)
        # `m.__func__` is the UNDERLYING function of a bound method -- the one
        # the class holds, without the receiver.
        if name == "__func__":
            # THE OBJECT THE CLASS HOLDS, looked up by the method's own name
            # -- not a copy. `c.m.__func__ is C.m` is the identity that says
            # what `__func__` means, and a fresh binding answers False to it.
            if obj.bound is None:
                return h._no_attr(obj, name)
            if isinstance(obj.bound, Instance):
                found = obj.bound.cls.find(obj.name)
                if found is not None:
                    return h._value(found)
            return h._no_attr(obj, name)
        if name == "__doc__":
            return h._value(obj.doc)
        # Annotations are erased by analysis, which is the whole point of the
        # two-path design -- so this is empty rather than absent.
        if name == "__annotations__":
            return h._new({})
        return h._no_attr(obj, name)
    if isinstance(obj, Exc):
        if name == "args":
            return h._new(() if not obj.has_arg else (obj.arg,))
        # Both are None when unset, never absent: `e.__cause__` is an
        # attribute every exception has, and code that reads it to decide
        # whether to print "the direct cause" would get an AttributeError.
        # `e.value` -- what a generator's `return` gave. Every exception has
        # it in CPython, so answering None keeps the bare form working too.
        if name == "value":
            return h._value(obj.arg if obj.has_arg else None)
        if name == "__context__":
            return h._value(obj.context)
        if name == "__cause__":
            return h._value(obj.cause)
        if name == "__suppress_context__":
            return h._bool(obj.suppress)
        if name == "__notes__":
            if obj.notes is None:
                return h._no_attr(obj, name)
            return h._value(obj.notes)
        # There is no traceback OBJECT -- no frames are recorded -- but an
        # exception that was raised HAS one, and `e.__traceback__ is not None`
        # is the test programs actually write. An empty tuple is the least
        # dishonest stand-in.
        if name == "__traceback__":
            return h._new(())
        if name == "__class__":
            return h._new(h._type_of(obj))
        return h._no_attr(obj, name)
    return h._no_attr(obj, name)


def _apy_default_repr(h, a):
    """`object.__repr__(x)` -- what a `__repr__` override overrode."""
    v = h._get(a[0], "apy_default_repr")
    if not isinstance(v, Instance):
        return h._new(h._text(v, True))
    return h._new(f"<{v.cls.name} object at 0x{id(v):x}>")


def _apy_default_eq(h, a):
    """IDENTITY, and `__hash__` agrees with it. That pairing is the contract:
    two objects that compare equal must hash equally, and the default
    satisfies it by comparing nothing but identity."""
    return h._bool(h._get(a[0], "apy_default_eq")
                   is h._get(a[1], "apy_default_eq"))


def _apy_default_hash(h, a):
    return h._int(id(h._get(a[0], "apy_default_hash")) >> 3)


def _apy_default_init(h, a):
    """`object.__init__(self)` does nothing: a subclass calling it is saying
    the base has no state to set up."""
    return h._none


def _apy_setattr(h, a):
    """`x.name = v`. `__setattr__` INTERCEPTS EVERY assignment, the mirror of
    `__getattribute__` -- and the default stays callable from within the
    override, which is the only way an override can actually store."""
    obj = h._get(a[0], "apy_setattr")
    if isinstance(obj, Instance) and obj.cls.find("__setattr__") is not None:
        return _user(h, lambda: h._value(obj._send(
            "__setattr__", str(h._get(a[1], "apy_setattr")),
            h._get(a[2], "apy_setattr"))))
    return _apy_default_setattr(h, a)


def _apy_default_setattr(h, a):
    obj = h._get(a[0], "apy_setattr")
    name = str(h._get(a[1], "apy_setattr"))
    value = h._get(a[2], "apy_setattr")
    if isinstance(obj, Instance):
        obj.dict[name] = value
        return h._none
    if isinstance(obj, Class):
        obj.dict[name] = value
        return h._none
    return h._no_attr(obj, name)


def _apy_call(h, a):
    """`a[1]` is the ADDRESS of an argument array, as `apy_print` takes one."""
    addr, n = int(a[1]), int(a[2])
    args = [h._get(h._interp.mem.read(addr + i * 8, _PTR), "apy_call")
            for i in range(n)]
    try:
        return h._value(h._invoke(h._get(a[0], "apy_call"), args))
    except _UserFailed:
        return 0


#: A slot no argument has reached yet, so the default can be told from a
#: legitimately-passed None. `f(c=3)` leaves a HOLE at `b`, and filling it
#: with None instead of `b`'s default is a different call.
_MISSING = object()


def _apy_call_kw(h, a):
    """`f(a, k=v, **d)`. The positional arguments are in the buffer; the
    keyword ones are a DICT built at the call site.

    The names are matched against the CALLEE's parameters, which is why the
    function object carries them: the callee is a value here, so which
    parameter `k=` names is known only to whatever it turns out to hold.
    """
    addr, argc = int(a[1]), int(a[2])

    def cell(i):
        return h._get(h._interp.mem.read(addr + i * 8, _PTR), "apy_call_kw")

    f = h._get(a[0], "apy_call_kw")
    args = [cell(i) for i in range(argc)]
    kwargs = dict(h._get(a[3], "apy_call_kw"))
    target, skip = f, 0
    if isinstance(f, Class):
        target, skip = f.find("__init__"), 1
    elif isinstance(f, Instance):
        target, skip = f.cls.find("__call__"), 1
    elif isinstance(f, Func) and f.bound is not None:
        skip = 1
    if not isinstance(target, Func):
        # No signature to match against. `_invoke` words both the missing
        # `__init__` and the non-callable, so let it.
        try:
            return h._value(h._invoke(f, args))
        except _UserFailed:
            return 0
    declared = (target.arity - (1 if target.vararg else 0)
                - (1 if target.kwarg else 0))
    want = max(0, declared - skip)
    # WHERE POSITIONS STOP. Names reach all of `want`; positions reach only
    # as far as the keyword-only tail, which is the whole of `*` in a
    # signature.
    bypos = max(0, want - target.kwonly)
    slots = list(args[:bypos])
    extra = args[bypos:]
    rest = {} if target.kwarg else None
    for name, value in kwargs.items():
        try:
            at = target.pnames.index(name) - skip
        except ValueError:
            at = -1
        # POSITIONAL-ONLY: the name is recorded so the message below can be
        # specific, but it does not match.
        posonly_hit = 0 <= at + skip < target.posonly
        if posonly_hit:
            at = -1
        if at < 0 or extra:
            if rest is None:
                if posonly_hit:
                    return h._fail(
                        "TypeError",
                        f"{target.name}() got some positional-only arguments "
                        f"passed as keyword arguments: '{name}'")
                return h._fail("TypeError",
                               f"{target.name}() got an unexpected keyword "
                               f"argument '{name}'")
            rest[name] = value
            continue
        if at < len(slots) and slots[at] is not _MISSING:
            return h._fail("TypeError", f"{target.name}() got multiple values "
                                        f"for argument '{name}'")
        while len(slots) <= at:
            slots.append(_MISSING)
        slots[at] = value
    for i, value in enumerate(slots):
        if value is not _MISSING:
            continue
        d = (i + skip) - (declared - len(target.defaults))
        if d < 0 or d >= len(target.defaults):
            pname = target.pnames[i + skip] or "?"
            return h._fail("TypeError", f"{target.name}() missing 1 required "
                                        f"positional argument: '{pname}'")
        slots[i] = target.defaults[d]
    try:
        return h._value(h._invoke(f, slots + extra, kwrest=rest, bound=True))
    except _UserFailed:
        return 0


# ── builtins over sequences ─────────────────────────────────────────────────
# Each of these is the Python operation on the real object, which is the whole
# reason the handle table holds real objects: `sorted` here IS `sorted`, so
# stability, the ordering rules and the TypeError all come from CPython rather
# than from a restatement of them that could drift from the C.

def _gen_cache(h, g):
    """A generator's elements, drained once. See `Gen.cache`."""
    if g.cache is None:
        got = _apy_gen_drain(h, [h._value(g)])
        if not got:
            return None
        g.cache = h._get(got, "apy_gen_drain")
    return g.cache


def _seq_items(h, v, where: str):
    """The elements of a sequence, or the KEYS of a dict -- what `apy_key_at`
    walks, so that every consumer of a container agrees on what iterating it
    yields."""
    if isinstance(v, Iterator):
        # CONSUMING, and from the current position -- the same rule the C's
        # `apy_step` follows. A consumer that read from zero would replay what
        # the cursor had already yielded and leave it unconsumed. Walked
        # through the protocol rather than reached into, because a `map`
        # cursor's elements do not exist until it is stepped.
        rest = _drain_cursor(h, v)
        if rest is None:
            return None
        v.i = len(rest)
        return list(rest)
    if isinstance(v, Gen):
        got = _gen_cache(h, v)
        return None if got is None else list(got)
    if isinstance(v, dict):
        return list(v)
    if isinstance(v, (list, tuple, set, frozenset, str, bytes)):
        return list(v)
    h._fail("TypeError", f"'{h.kind_name(v)}' object is not iterable")
    return None


def _apy_sorted(h, a):
    items = _seq_items(h, h._get(a[0], "apy_sorted"), "apy_sorted")
    if items is None:
        return 0
    try:
        return h._new(sorted(items))
    except TypeError as exc:
        return h._fail_like(exc)


def _apy_min(h, a):
    items = _seq_items(h, h._get(a[0], "apy_min"), "apy_min")
    if items is None:
        return 0
    try:
        return h._value(min(items))
    except (TypeError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_max(h, a):
    items = _seq_items(h, h._get(a[0], "apy_max"), "apy_max")
    if items is None:
        return 0
    try:
        return h._value(max(items))
    except (TypeError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_sum(h, a):
    items = _seq_items(h, h._get(a[0], "apy_sum"), "apy_sum")
    if items is None:
        return 0
    try:
        return h._value(sum(items))
    except TypeError as exc:
        return h._fail_like(exc)


def _apy_sum_from(h, a):
    items = _seq_items(h, h._get(a[0], "apy_sum_from"), "apy_sum_from")
    if items is None:
        return 0
    try:
        return h._value(sum(items, h._get(a[1], "apy_sum_from")))
    except TypeError as exc:
        return h._fail_like(exc)


def _argv(h, a, where: str, n: int, at: int = 1):
    """The values in a stack array, as the C's `apy_value *` argument is."""
    addr = int(a[at])
    return [h._get(h._interp.mem.read(addr + i * 8, _PTR), where)
            for i in range(n)]


def _apy_extreme_n(h, a):
    """`min(a, b, ...)` -- the MULTI-ARGUMENT form, which compares the
    arguments themselves rather than the elements of one of them."""
    args = _argv(h, a, "apy_extreme_n", int(a[1]), 0)
    if not args:
        return h._fail("TypeError", "min expected at least 1 argument")
    try:
        return h._value((max if int(a[2]) else min)(args))
    except (TypeError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_extreme_or(h, a):
    """`min(xs, default=v)`. Only an EMPTY iterable reaches the default."""
    items = _seq_items(h, h._get(a[0], "apy_extreme_or"), "apy_extreme_or")
    if items is None:
        return 0
    if not items:
        return a[2]
    key = h._get(a[1], "apy_extreme_or")
    pick = max if int(a[3]) else min
    try:
        if key is None:
            return h._value(pick(items))
        return _user(h, lambda: h._value(
            pick(items, key=lambda v: h._invoke(key, [v]))))
    except (TypeError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_enumerate(h, a):
    """`enumerate(xs, start)` -- LAZY, and consumed once, like the C's."""
    src = _apy_getiter(h, [a[0]])
    if not src:
        return 0
    return h._new(Iterator(h._get(src, "apy_enumerate"), None,
                           Iterator.ENUMERATE, int(a[1])))


def _apy_reversed(h, a):
    """`reversed(xs)` -- a LIST, not a cursor, because reversing needs the
    length: there is nothing to reverse until the source has been walked, so
    the laziness the other four have is not available here."""
    items = _seq_items(h, h._get(a[0], "apy_reversed"), "apy_reversed")
    if items is None:
        return 0
    return h._new(list(reversed(items)))


def _apy_zip_n(h, a):
    """`zip(...)` -- LAZY, stopping at the shortest. `strict` reports an
    uneven zip instead, which is the whole point of PEP 618: the lossiness is
    useful and is also the bug, so the caller says which it meant."""
    cursors = []
    for v in _argv(h, a, "apy_zip_n", int(a[1]), 0):
        got = _apy_getiter(h, [h._value(v)])
        if not got:
            return 0
        cursors.append(h._get(got, "apy_zip_n"))
    return h._new(Iterator(cursors, int(a[2]) != 0, Iterator.ZIP))


def _apy_zip2(h, a):
    return _apy_zip_n(h, [a[0], 0, 0]) if False else _zip_two(h, a)


def _zip_two(h, a):
    cursors = []
    for handle in (a[0], a[1]):
        got = _apy_getiter(h, [handle])
        if not got:
            return 0
        cursors.append(h._get(got, "apy_zip2"))
    return h._new(Iterator(cursors, False, Iterator.ZIP))


def _apy_range(h, a):
    start, stop, step = int(a[0]), int(a[1]), int(a[2])
    if step == 0:
        return h._fail("ValueError", "range() arg 3 must not be zero")
    return h._new(list(range(start, stop, step)))


def _apy_abs(h, a):
    v = h._get(a[0], "apy_abs")
    if isinstance(v, Instance) and v.cls.find("__abs__") is not None:
        return _user(h, lambda: h._value(v._send("__abs__")))
    # `abs(complex)` is its MODULUS, and a float -- the one kind for which abs
    # changes the type rather than the sign.
    if not isinstance(v, (int, float, complex)):
        return h._fail("TypeError",
                       f"bad operand type for abs(): '{h.kind_name(v)}'")
    return h._value(abs(v))


def _apy_round(h, a):
    v = h._get(a[0], "apy_round")
    if isinstance(v, bool) or isinstance(v, int):
        return h._int(int(v))
    if not isinstance(v, float):
        return h._fail("TypeError",
                       f"type '{h.kind_name(v)}' doesn't define __round__ method")
    # Python's `round` is half-to-EVEN and returns an int with no digits
    # argument. C's `round` is half-away-from-zero, which is why the C has its
    # own; here the builtin already has the right rule.
    return h._int(round(v))


def _apy_round_to(h, a):
    """`round(x, n)` -- a different function from `round(x)`, and not only in
    precision: this one answers a float where that one answers an int."""
    v = h._get(a[0], "apy_round_to")
    nd = h._get(a[1], "apy_round_to")
    if nd is None:
        return _apy_round(h, a)
    if isinstance(nd, bool) or not isinstance(nd, int):
        return h._fail("TypeError", f"'{h.kind_name(nd)}' object cannot be "
                                    f"interpreted as an integer")
    if isinstance(v, bool) or isinstance(v, int):
        return h._int(round(v, nd))
    if not isinstance(v, float):
        return h._fail("TypeError", f"type '{h.kind_name(v)}' doesn't define "
                                    f"__round__ method")
    return h._new(round(v, nd))


def _apy_is_subclass(h, a):
    x = h._get(a[0], "apy_is_subclass")
    y = h._get(a[1], "apy_is_subclass")
    if not isinstance(x, Class):
        return h._fail("TypeError", "issubclass() arg 1 must be a class")
    if not isinstance(y, Class):
        return h._fail("TypeError", "issubclass() arg 2 must be a class or "
                                    "tuple of classes")
    seen = x
    while isinstance(seen, Class):
        if seen is y:
            return h._bool(True)
        seen = seen.base
    # An EXCEPTION type has no base pointer -- the builtin hierarchy is a
    # table of NAMES, because `raise` and `except` match on the name and never
    # hold a class. So the same question is asked again, of that table.
    return h._bool(y.name in _exc_chain(h, x.name))


def _apy_exc_type(h, a):
    """A builtin exception NAME as a value. Interned by `_type_of`, so the
    same name is the same object and `type(e) is ValueError` holds."""
    return h._new(h._type_of(Exc(str(h._get(a[0], "apy_exc_type")), None,
                                 False)))


def _apy_vars(h, a):
    obj = h._get(a[0], "apy_vars")
    # A CLASS has a `__dict__` too, holding the names its body bound, and
    # `"x" in vars(C)` is how a program asks whether the class defines one.
    if isinstance(obj, Class):
        return h._new(dict(obj.dict))
    if not isinstance(obj, Instance):
        return h._fail("TypeError",
                       "vars() argument must have __dict__ attribute")
    return h._new(dict(obj.dict))


def _apy_delattr(h, a):
    obj = h._get(a[0], "apy_delattr")
    if isinstance(obj, Instance) and obj.cls.find("__delattr__") is not None:
        return _user(h, lambda: h._value(obj._send(
            "__delattr__", str(h._get(a[1], "apy_delattr")))))
    return _apy_default_delattr(h, a)


def _apy_default_delattr(h, a):
    obj = h._get(a[0], "apy_delattr")
    name = str(h._get(a[1], "apy_delattr"))
    if not isinstance(obj, Instance) or name not in obj.dict:
        return h._fail("AttributeError",
                       f"'{h.kind_name(obj)}' object has no attribute "
                       f"'{name}'")
    del obj.dict[name]
    return h._none


def _apy_iter_until(h, a):
    """`iter(f, sentinel)` -- the CALLABLE form. The calls all happen now, so
    what comes back is a cursor over their results rather than something that
    will call `f` again later; a generator is the shape that would."""
    fn = h._get(a[0], "apy_iter_until")
    stop = h._get(a[1], "apy_iter_until")
    out = []
    try:
        for _ in range(1000000):
            v = h._invoke(fn, [])
            if v == stop:
                break
            out.append(v)
    except _UserFailed:
        return 0
    return h._new(Iterator(out))


def _apy_isinstance(h, a):
    # A TUPLE OF TYPES means ANY OF THESE, and there is no ambiguity with
    # asking about the tuple type itself: `isinstance(x, tuple)` arrives as
    # the STRING "tuple", because a builtin kind has no value form.
    v = h._get(a[0], "apy_isinstance")
    spec = h._get(a[1], "apy_isinstance")
    if isinstance(spec, tuple):
        for element in spec:
            got = _apy_isinstance(h, [a[0], h._value(element)])
            if not got:
                return 0
            if h._get(got, "apy_isinstance"):
                return h._bool(True)
        return h._bool(False)
    want = h._get(a[1], "apy_isinstance")
    # A real TYPE OBJECT, for a user class: the frontend passes the class
    # itself when the name resolves to one, and its text otherwise. Comparing
    # names would make two classes both called `Node` instances of each other.
    if isinstance(want, Class):
        return h._bool(isinstance(v, Instance) and v.cls.is_sub(want))
    have = h.kind_name(v)
    # An INSTANCE never matches a builtin name. Its kind_name is its class's
    # name, so without this a class called `int` would answer True.
    if isinstance(v, Instance):
        return h._bool(want == "object")
    if have == want or want == "object":
        return h._bool(True)
    # bool is a SUBCLASS of int in Python, so this is not a name comparison.
    if isinstance(v, bool) and want == "int":
        return h._bool(True)
    if isinstance(v, Exc) and (want in h.user_exc or v.name in h.user_exc):
        return h._bool(want in _exc_chain(h, v.name))
    if isinstance(v, Exc):
        # An exception instance is an instance of every base in its chain.
        # Resolved against Python's own classes, which is what
        # `_apy_error_matches` does -- one source for the hierarchy, so the
        # two answers cannot drift.
        have_cls = getattr(__import__("builtins"), v.name, None)
        want_cls = getattr(__import__("builtins"), want, None)
        if (isinstance(want_cls, type) and issubclass(want_cls, BaseException)
                and isinstance(have_cls, type)
                and issubclass(have_cls, BaseException)):
            return h._bool(issubclass(have_cls, want_cls))
    return h._bool(False)


def _apy_slice(h, a):
    v = h._get(a[0], "apy_slice")
    start, stop, step = int(a[1]), int(a[2]), int(a[3])
    has_start, has_stop = int(a[4]), int(a[5])
    if step == 0:
        return h._fail("ValueError", "slice step cannot be zero")
    if not isinstance(v, (list, tuple, str, bytes)):
        return h._fail("TypeError",
                       f"'{h.kind_name(v)}' object is not subscriptable")
    sl = slice(start if has_start else None,
               stop if has_stop else None, step)
    return h._new(v[sl])


# ── list and dict methods ───────────────────────────────────────────────────

def _apy_list_pop(h, a):
    v = h._get(a[0], "apy_list_pop")
    if not isinstance(v, list):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute 'pop'")
    if not v:
        return h._fail("IndexError", "pop from empty list")
    index = int(h._get(a[1], "apy_list_pop")) if int(a[2]) else -1
    try:
        return h._value(v.pop(index))
    except IndexError:
        return h._fail("IndexError", "pop index out of range")


def _apy_index_of(h, a):
    v = h._get(a[0], "apy_index_of")
    item = h._get(a[1], "apy_index_of")
    items = _seq_items(h, v, "apy_index_of")
    if items is None:
        return 0
    for i, x in enumerate(items):
        if x == item:
            return h._int(i)
    return h._fail("ValueError", f"{h._text(item, True)} is not in list")


def _apy_count_of(h, a):
    seq = h._get(a[0], "apy_count_of")
    item = h._get(a[1], "apy_count_of")
    # SUBSTRING counting for a str, element counting for a sequence -- the
    # same split `index` makes, and the reason `'aaaa'.count('aa')` is 2 and
    # not 0. Walking a str as a list of characters answered 0 for every
    # multi-character needle.
    if isinstance(seq, (str, bytes)):
        if not isinstance(item, type(seq)):
            return h._fail("TypeError",
                           f"must be {h.kind_name(seq)}, not "
                           f"{h.kind_name(item)}")
        return h._int(seq.count(item))
    items = _seq_items(h, seq, "apy_count_of")
    if items is None:
        return 0
    return h._int(sum(1 for x in items if x == item))


def _apy_list_remove(h, a):
    v = h._get(a[0], "apy_list_remove")
    item = h._get(a[1], "apy_list_remove")
    if not isinstance(v, list):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute 'remove'")
    try:
        v.remove(item)
    except ValueError:
        return h._fail("ValueError", "list.remove(x): x not in list")
    return h._none


def _apy_dict_parts(h, a):
    v = h._get(a[0], "apy_dict_parts")
    if not isinstance(v, dict):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute")
    which = int(a[1])
    # Lists, not views -- snapshots, matching the C. A view is live and these
    # are not, which differs only if the dict is mutated while one is held.
    if which == 0:
        return h._new(list(v.keys()))
    if which == 1:
        return h._new(list(v.values()))
    return h._new([(k, val) for k, val in v.items()])


def _apy_dict_get_or(h, a):
    v = h._get(a[0], "apy_dict_get_or")
    if not isinstance(v, dict):
        return h._fail("AttributeError",
                       f"'{h.kind_name(v)}' object has no attribute 'get'")
    key = h._get(a[1], "apy_dict_get_or")
    try:
        hash(key)
    except TypeError as exc:
        return h._fail_like(exc)
    return a[2] if key not in v else h._value(v[key])


#: `apy_name` -> the function that implements it.
#:
#: DERIVED from the definitions above rather than listed, because the naming
#: is exact -- `_apy_foo` implements `apy_foo` -- and a hand-written list of
#: two hundred entries is a second place for the same fact to live. The one
#: it drifts from is the runtime, and `tests/asmpython/unit/test_objects_host`
#: is the ratchet that catches that: every symbol `link/objects.py` exports
#: must be reachable here.
#:
#: The arithmetic and comparison operators are added separately: they share
#: one implementation parameterised by the Python operator, so there is no
#: `_apy_add` to find.
_TABLE = {name[1:]: fn for name, fn in list(globals().items())
          if name.startswith("_apy_") and callable(fn)}

_TABLE.update({
    "apy_add": _binop("apy_add", lambda x, y: x + y, "+"),
    "apy_sub": _binop("apy_sub", lambda x, y: x - y, "-"),
    "apy_mul": _binop("apy_mul", lambda x, y: x * y, "*"),
    "apy_truediv": _binop("apy_truediv", lambda x, y: x / y, "/"),
    "apy_floordiv": _binop("apy_floordiv", lambda x, y: x // y, "//"),
    "apy_mod": _binop("apy_mod", lambda x, y: x % y, "%"),
    "apy_pow": _binop("apy_pow", _pow, "**"),
    "apy_bitand": _binop("apy_bitand", lambda x, y: x & y, "&"),
    "apy_bitor": _binop("apy_bitor", lambda x, y: x | y, "|"),
    "apy_bitxor": _binop("apy_bitxor", lambda x, y: x ^ y, "^"),
    "apy_lshift": _binop("apy_lshift", lambda x, y: x << y, "<<"),
    "apy_rshift": _binop("apy_rshift", lambda x, y: x >> y, ">>"),
    "apy_eq": _cmpop("apy_eq", lambda x, y: x == y),
    "apy_ne": _cmpop("apy_ne", lambda x, y: x != y),
    "apy_lt": _cmpop("apy_lt", lambda x, y: x < y),
    "apy_le": _cmpop("apy_le", lambda x, y: x <= y),
    "apy_gt": _cmpop("apy_gt", lambda x, y: x > y),
    "apy_ge": _cmpop("apy_ge", lambda x, y: x >= y),
})

_TABLE.update({
    "apy_sorted": _apy_sorted,
    "apy_min": _apy_min,
    "apy_max": _apy_max,
    "apy_sum": _apy_sum,
    "apy_reversed": _apy_reversed,
    "apy_enumerate": _apy_enumerate,
    "apy_zip2": _apy_zip2,
    "apy_range": _apy_range,
    "apy_abs": _apy_abs,
    "apy_round": _apy_round,
    "apy_isinstance": _apy_isinstance,
    "apy_slice": _apy_slice,
    "apy_list_pop": _apy_list_pop,
    "apy_index_of": _apy_index_of,
    "apy_count_of": _apy_count_of,
    "apy_list_remove": _apy_list_remove,
    "apy_dict_parts": _apy_dict_parts,
    "apy_dict_get_or": _apy_dict_get_or,
})


# ── str methods, and set algebra ────────────────────────────────────────────
# Every one of these is the Python method on the real object. That is the whole
# argument for a handle table over real objects rather than a reimplementation:
# `'ab'.split(',')` here IS `str.split`, so the empty-separator ValueError, the
# whitespace-runs rule and the exact return shapes come from CPython instead of
# from a second description of them that could drift from the C's.
#
# The spec is (symbol, Python method, argument count) and the wrappers are
# built from it, because forty near-identical functions written out is forty
# chances to bind one to the wrong method.

_STR_METHOD_SPEC = (
    ("apy_str_upper", "upper", 0), ("apy_str_lower", "lower", 0),
    ("apy_str_title", "title", 0), ("apy_str_capitalize", "capitalize", 0),
    ("apy_str_swapcase", "swapcase", 0), ("apy_str_casefold", "casefold", 0),
    ("apy_str_strip", "strip", 0), ("apy_str_lstrip", "lstrip", 0),
    ("apy_str_rstrip", "rstrip", 0),
    ("apy_str_strip_chars", "strip", 1),
    ("apy_str_lstrip_chars", "lstrip", 1),
    ("apy_str_rstrip_chars", "rstrip", 1),
    ("apy_str_split_ws", "split", 0), ("apy_str_split", "split", 1),
    ("apy_str_split_n", "split", 2),
    ("apy_str_rsplit_ws", "rsplit", 0), ("apy_str_rsplit", "rsplit", 1),
    ("apy_str_rsplit_n", "rsplit", 2),
    ("apy_str_splitlines", "splitlines", 0),
    ("apy_str_splitlines_keep", "splitlines", 1),
    ("apy_str_partition", "partition", 1),
    ("apy_str_rpartition", "rpartition", 1),
    ("apy_str_join", "join", 1),
    ("apy_str_replace", "replace", 2), ("apy_str_replace_n", "replace", 3),
    ("apy_str_find", "find", 1), ("apy_str_find2", "find", 2),
    ("apy_str_find3", "find", 3),
    ("apy_str_rfind", "rfind", 1), ("apy_str_rfind2", "rfind", 2),
    ("apy_str_rfind3", "rfind", 3),
    ("apy_str_rindex", "rindex", 1),
    ("apy_str_startswith", "startswith", 1),
    ("apy_str_startswith2", "startswith", 2),
    ("apy_str_startswith3", "startswith", 3),
    ("apy_str_endswith", "endswith", 1),
    ("apy_str_endswith2", "endswith", 2),
    ("apy_str_endswith3", "endswith", 3),
    ("apy_str_count2", "count", 2), ("apy_str_count3", "count", 3),
    ("apy_str_zfill", "zfill", 1),
    ("apy_str_center", "center", 1), ("apy_str_center_fill", "center", 2),
    ("apy_str_ljust", "ljust", 1), ("apy_str_ljust_fill", "ljust", 2),
    ("apy_str_rjust", "rjust", 1), ("apy_str_rjust_fill", "rjust", 2),
    ("apy_str_removeprefix", "removeprefix", 1),
    ("apy_str_removesuffix", "removesuffix", 1),
    ("apy_str_isalpha", "isalpha", 0), ("apy_str_isdigit", "isdigit", 0),
    ("apy_str_isdecimal", "isdecimal", 0),
    ("apy_str_isnumeric", "isnumeric", 0),
    ("apy_str_isalnum", "isalnum", 0), ("apy_str_isspace", "isspace", 0),
    ("apy_str_islower", "islower", 0), ("apy_str_isupper", "isupper", 0),
    ("apy_str_istitle", "istitle", 0), ("apy_str_isascii", "isascii", 0),
    ("apy_str_isprintable", "isprintable", 0),
    ("apy_str_isidentifier", "isidentifier", 0),
)


def _make_str_method(symbol: str, method: str, argc: int):
    def binding(h, a, _m=method, _n=argc, _sym=symbol):
        receiver = h._get(a[0], _sym)
        if not isinstance(receiver, str):
            return h._fail(
                "AttributeError",
                f"'{h.kind_name(receiver)}' object has no attribute '{_m}'")
        args = [h._get(a[i + 1], _sym) for i in range(_n)]
        try:
            return h._value(getattr(receiver, _m)(*args))
        except (TypeError, ValueError) as exc:
            return h._fail_like(exc)
    return binding


for _sym, _method, _argc in _STR_METHOD_SPEC:
    _TABLE[_sym] = _make_str_method(_sym, _method, _argc)


# Set algebra. The pair operations reject a non-set operand the way Python's
# METHODS do not -- `{1}.union([2])` accepts an iterable while `{1} | [2]` does
# not -- so these follow the C, which is operator-shaped throughout.
_SET_OP_SPEC = (
    ("apy_set_union", "union"),
    ("apy_set_intersection", "intersection"),
    ("apy_set_difference", "difference"),
    ("apy_set_symdiff", "symmetric_difference"),
    ("apy_set_issubset", "issubset"),
    ("apy_set_issuperset", "issuperset"),
    ("apy_set_isdisjoint", "isdisjoint"),
)


def _make_set_op(symbol: str, method: str):
    def binding(h, a, _m=method, _sym=symbol):
        left = h._get(a[0], _sym)
        right = h._get(a[1], _sym)
        if not isinstance(left, (set, frozenset)):
            return h._fail(
                "AttributeError",
                f"'{h.kind_name(left)}' object has no attribute '{_m}'")
        if not isinstance(right, (set, frozenset)):
            return h._fail(
                "TypeError",
                f"unsupported operand type(s): '{h.kind_name(left)}' and "
                f"'{h.kind_name(right)}'")
        result = getattr(left, _m)(right)
        # A frozenset operand keeps the LEFT operand's kind, as Python does.
        if isinstance(result, (set, frozenset)) and isinstance(left, frozenset):
            result = frozenset(result)
        return h._new(result) if not isinstance(result, bool) else h._bool(result)
    return binding


for _sym, _method in _SET_OP_SPEC:
    _TABLE[_sym] = _make_set_op(_sym, _method)


def _apy_set_new(h, a):
    return h._new(set())


def _apy_frozenset_new(h, a):
    return h._new(frozenset())


def _apy_set_push(h, a):
    s = h._get(a[0], "apy_set_push")
    item = h._get(a[1], "apy_set_push")
    try:
        hash(item)
    except TypeError as exc:
        return h._fail_like(exc)
    if isinstance(s, set):
        s.add(item)
        return h._none
    return h._fail("AttributeError",
                   f"'{h.kind_name(s)}' object has no attribute 'add'")


def _apy_set_discard(h, a):
    s = h._get(a[0], "apy_set_discard")
    if not isinstance(s, set):
        return h._fail("AttributeError",
                       f"'{h.kind_name(s)}' object has no attribute 'discard'")
    s.discard(h._get(a[1], "apy_set_discard"))
    return h._none


def _to_set(h, a, frozen: bool):
    v = h._get(a[0], "apy_to_set")
    if not isinstance(v, (list, tuple, set, frozenset, dict, str)):
        return h._fail("TypeError",
                       f"'{h.kind_name(v)}' object is not iterable")
    try:
        return h._new(frozenset(v) if frozen else set(v))
    except TypeError as exc:
        return h._fail_like(exc)


_TABLE.update({
    "apy_set_new": _apy_set_new,
    "apy_frozenset_new": _apy_frozenset_new,
    "apy_set_push": _apy_set_push,
    "apy_set_add": _apy_set_push,
    "apy_set_discard": _apy_set_discard,
    "apy_to_set": lambda h, a: _to_set(h, a, False),
    "apy_to_frozenset": lambda h, a: _to_set(h, a, True),
})


#: The class machinery's bindings, registered where they are DEFINED rather
#: than in the table above. They live at the bottom of the file because they
#: need `Instance` and the re-entry into the interpreter, and a forward
#: reference in the table would be a NameError at import -- which is the same
#: protection the written-out table gives, arriving in the same way.
_TABLE.update({
    "apy_cell_new": _apy_cell_new,
    "apy_cell_get": _apy_cell_get,
    "apy_cell_set": _apy_cell_set,
    "apy_func_new": _apy_func_new,
    "apy_func_cell": _apy_func_cell,
    "apy_func_param": _apy_func_param,
    "apy_func_kwarg": _apy_func_kwarg,
    "apy_func_kwonly": _apy_func_kwonly,
    "apy_func_posonly": _apy_func_posonly,
    "apy_func_doc": _apy_func_doc,
    "apy_update": _apy_update,
    "apy_clear": _apy_clear,
    "apy_copy": _apy_copy,
    "apy_hash": _apy_hash,
    "apy_iadd": _apy_iadd,
    "apy_iop": _apy_iop,
    "apy_list_insert": _apy_list_insert,
    "apy_list_sort": _apy_list_sort,
    "apy_list_reverse": _apy_list_reverse,
    "apy_setdefault": _apy_setdefault,
    "apy_format": _apy_format,
    "apy_str_format": _apy_str_format,
    "apy_str_encode": _apy_str_encode,
    "apy_bytes_decode": _apy_bytes_decode,
    "apy_bytes_hex": _apy_bytes_hex,
    "apy_bytes_fromhex": _apy_bytes_fromhex,
    "apy_to_bytes_n": _apy_to_bytes_n,
    "apy_as_integer_ratio": _apy_as_integer_ratio,
    "apy_str_expandtabs": _apy_str_expandtabs,
    "apy_math_sqrt": _math1("apy_math_sqrt", math.sqrt),
    "apy_math_floor": _math1("apy_math_floor", math.floor),
    "apy_math_ceil": _math1("apy_math_ceil", math.ceil),
    "apy_math_trunc": _math1("apy_math_trunc", math.trunc),
    "apy_math_fabs": _math1("apy_math_fabs", math.fabs),
    "apy_math_isnan": _math1("apy_math_isnan", math.isnan),
    "apy_math_isinf": _math1("apy_math_isinf", math.isinf),
    "apy_math_isfinite": _math1("apy_math_isfinite", math.isfinite),
    "apy_math_isqrt": _math1("apy_math_isqrt", math.isqrt, want_int=True),
    "apy_math_factorial": _math1("apy_math_factorial", math.factorial,
                                 want_int=True),
    "apy_math_exp": _math1("apy_math_exp", math.exp),
    "apy_math_log": _math1("apy_math_log", math.log),
    "apy_math_log2": _math1("apy_math_log2", math.log2),
    "apy_math_log10": _math1("apy_math_log10", math.log10),
    "apy_math_sin": _math1("apy_math_sin", math.sin),
    "apy_math_cos": _math1("apy_math_cos", math.cos),
    "apy_math_tan": _math1("apy_math_tan", math.tan),
    "apy_math_atan": _math1("apy_math_atan", math.atan),
    "apy_math_degrees": _math1("apy_math_degrees", math.degrees),
    "apy_math_radians": _math1("apy_math_radians", math.radians),
    "apy_math_gcd": _math2("apy_math_gcd", math.gcd, want_int=True),
    "apy_math_lcm": _math2("apy_math_lcm", math.lcm, want_int=True),
    "apy_math_copysign": _math2("apy_math_copysign", math.copysign),
    "apy_math_pow": _math2("apy_math_pow", math.pow),
    "apy_math_atan2": _math2("apy_math_atan2", math.atan2),
    "apy_math_hypot": _math2("apy_math_hypot", math.hypot),
    "apy_math_isclose": _apy_math_isclose,
    "apy_is_integer": _apy_is_integer,
    "apy_conjugate": _apy_conjugate,
    "apy_func_default": _apy_func_default,
    "apy_env_cell": _apy_env_cell,
    "apy_call": _apy_call,
    "apy_sum_from": _apy_sum_from,
    "apy_extreme_n": _apy_extreme_n,
    "apy_extreme_or": _apy_extreme_or,
    "apy_zip_n": _apy_zip_n,
    "apy_round_to": _apy_round_to,
    "apy_is_subclass": _apy_is_subclass,
    "apy_exc_type": _apy_exc_type,
    "apy_vars": _apy_vars,
    "apy_delattr": _apy_delattr,
    "apy_iter_until": _apy_iter_until,
    "apy_call_kw": _apy_call_kw,
    "apy_type_new": _apy_type_new,
    "apy_type_set": _apy_type_set,
    "apy_instance_new": _apy_instance_new,
    "apy_type_object": _apy_type_object,
    "apy_enter": _apy_enter,
    "apy_exit": _apy_exit,
    "apy_default_getattr": _apy_default_getattr,
    "apy_default_repr": _apy_default_repr,
    "apy_default_eq": _apy_default_eq,
    "apy_default_hash": _apy_default_hash,
    "apy_default_init": _apy_default_init,
    "apy_getattr": _apy_getattr,
    "apy_is_instance": _apy_is_instance,
    "apy_exc_register": _apy_exc_register,
    "apy_default_setattr": _apy_default_setattr,
    "apy_default_delattr": _apy_default_delattr,
    "apy_setattr": _apy_setattr,
    "apy_super": _apy_super,
})


# ── bytes ───────────────────────────────────────────────────────────────────
# A `bytes` value is a Python `bytes`, so every operation on it below is the
# Python operation -- the escaping in its repr, the octet ordering, the
# int-from-indexing and bytes-from-slicing asymmetry, all of them CPython's
# rather than a second description of them.
#
# The one thing that needs saying: `kind_name` answers "bytes" for these
# because `type(b'') is bytes`, and `apy_text` must give the REPR even when
# asked for `str` -- `str(b'ab')` is "b'ab'", not "ab". Both fall out of
# holding a real `bytes` and calling `repr` on it.

def _apy_bytes_literal(h, a):
    """A bytes literal read out of the program's own data.

    The LENGTH is passed rather than measured, because a bytes literal may
    contain a NUL -- `b"a\\x00b"` is three bytes -- and stopping at one would
    silently truncate every literal that has one. That is the difference
    between bytes and str, so getting it wrong here would erase the reason the
    kind exists.
    """
    addr, n = int(a[0]), int(a[1])
    return h._new(bytes(h._interp.mem.buf[addr:addr + n]))


_TABLE["apy_bytes_literal"] = _apy_bytes_literal


def _apy_delitem(h, a):
    """`del d[k]` and `del xs[i]`.

    A tuple is refused: immutability is the whole distinction from a list, and
    Python's own message for it names item DELETION rather than assignment.
    """
    seq = h._get(a[0], "apy_delitem")
    key = h._get(a[1], "apy_delitem")
    if isinstance(seq, dict):
        try:
            hash(key)
        except TypeError as exc:
            return h._fail_like(exc)
        if key not in seq:
            return h._fail("KeyError", h._text(key, True))
        del seq[key]
        return h._none
    if not isinstance(seq, list):
        return h._fail("TypeError",
                       f"'{h.kind_name(seq)}' object doesn't support item "
                       f"deletion")
    i = int(key)
    if i < 0:
        i += len(seq)
    if not 0 <= i < len(seq):
        return h._fail("IndexError", "list assignment index out of range")
    del seq[i]
    return h._none


_TABLE["apy_delitem"] = _apy_delitem


def _apy_call_spread(h, a):
    """`f(*xs)`. The arguments arrive as a LIST, not an address.

    That is the whole difference from `apy_call`: a starred call's argument
    count is a value rather than a constant, so the frontend has no number to
    emit and builds a list instead.
    """
    args = h._get(a[1], "apy_call_spread")
    try:
        return h._value(h._invoke(h._get(a[0], "apy_call_spread"), list(args)))
    except _UserFailed:
        return 0


def _apy_extend(h, a):
    """Every element of a sequence, appended. A dict spreads its KEYS, which
    is what iterating one yields."""
    seq = h._get(a[0], "apy_extend")
    other = h._get(a[1], "apy_extend")
    # A TUPLE UNDER CONSTRUCTION counts. `(*xs, 3)` is `apy_tuple_new` plus a
    # push per element, and a starred one extends the same partly-built cell --
    # the C fills a tuple by pushing too, so refusing one here made the two
    # paths disagree on a display the compiled program built happily.
    if not isinstance(seq, (list, tuple)):
        return h._fail("AttributeError",
                       f"'{h.kind_name(seq)}' object has no attribute 'extend'")
    if not isinstance(other, (list, tuple, set, frozenset, str, bytes, dict,
                              Iterator)):
        return h._fail("TypeError",
                       f"'{h.kind_name(other)}' object is not iterable")
    items = _seq_items(h, other, "apy_extend")
    if items is None:
        return 0
    if isinstance(seq, list):
        seq.extend(items)
        return h._none
    # A tuple grows by REPLACING the cell, which is what `apy_seq_push` does
    # and what keeps the handle -- and so the identity -- unchanged.
    for item in items:
        _apy_seq_push(h, [a[0], h._new(item)])
    return h._none


_TABLE["apy_call_spread"] = _apy_call_spread
_TABLE["apy_extend"] = _apy_extend


def _apy_make_exc0(h, a):
    """An exception with NO argument, as distinct from one whose argument is
    None. `E().args` is `()` and `E(None).args` is `(None,)`, so the two
    cannot share a constructor -- see `Exc.has_arg`."""
    name = h._get(a[0], "apy_make_exc0")
    return h._new(Exc(name, None, has_arg=False))


def _apy_int_literal(h, a):
    """An integer literal too large for a machine word, as decimal text.

    The IR's `const` is a machine word, so the frontend cannot emit one --
    `9223372036854775808` wrapped to its negative. Python's own `int()` parses
    it here, which is exactly what the C's digit-at-a-time parser computes.
    """
    addr, n, neg = int(a[0]), int(a[1]), int(a[2])
    digits = bytes(h._interp.mem.buf[addr:addr + n]).decode("ascii")
    return h._int(-int(digits) if neg else int(digits))


_TABLE["apy_make_exc0"] = _apy_make_exc0
_TABLE["apy_int_literal"] = _apy_int_literal


def _apy_from_complex(h, a):
    return h._new(complex(float(a[0]), float(a[1])))


def _apy_complex_of(h, a):
    """`complex(re, im)` from two runtime values.

    NOT `re + im * 1j`: that loses a signed zero, and the sign of a zero is
    observable in the repr -- `complex(0, -0.0)` is `-0j`. Two real arguments
    are stored as they are, which is what the C does for the same reason.
    """
    re = h._get(a[0], "apy_complex_of")
    im = h._get(a[1], "apy_complex_of")
    for v in (re, im):
        if not isinstance(v, (int, float, complex)):
            return h._fail("TypeError",
                           f"complex() argument must be a string or a "
                           f"number, not '{h.kind_name(v)}'")
    if not isinstance(re, complex) and not isinstance(im, complex):
        return h._new(complex(re, im))
    return h._new(complex(re) + complex(im) * 1j)


_TABLE["apy_from_complex"] = _apy_from_complex
_TABLE["apy_complex_of"] = _apy_complex_of


# ── the small builtins ──────────────────────────────────────────────────────
# Each is the Python function on the real object. `hash` is the one that
# cannot be: Python's is salted per process and the C's is not, so two runs of
# the same program would disagree with each other -- see below.

def _apy_ord(h, a):
    v = h._get(a[0], "apy_ord")
    if isinstance(v, bytes):
        if len(v) != 1:
            return h._fail("TypeError", "ord() expected a character, but "
                                        "string of length != 1 found")
        return h._int(v[0])
    if not isinstance(v, str):
        return h._fail("TypeError", f"ord() expected string of length 1, but "
                                    f"{h.kind_name(v)} found")
    if len(v) != 1:
        return h._fail("TypeError", "ord() expected a character, but string "
                                    "of length != 1 found")
    return h._int(ord(v))


def _apy_chr(h, a):
    """A code point as a one-character str. UTF-8 underneath, because that is
    how a str is stored -- so this is one to four bytes and `len` decodes them
    again. Refusing anything above ASCII, which is what this did, made
    `chr(233)` an error on a runtime that handles the byte fine."""
    v = h._get(a[0], "apy_chr")
    if isinstance(v, bool) or not isinstance(v, int):
        return h._fail("TypeError",
                       f"an integer is required (got type {h.kind_name(v)})")
    if not 0 <= v <= 0x10FFFF:
        return h._fail("ValueError", "chr() arg not in range(0x110000)")
    return h._new(chr(v))


def _apy_callable(h, a):
    v = h._get(a[0], "apy_callable")
    if isinstance(v, (Func, Class)):
        return h._bool(True)
    if isinstance(v, Instance):
        return h._bool(v.cls.find("__call__") is not None)
    return h._bool(False)


def _apy_hasattr(h, a):
    got = _apy_getattr(h, a)
    if got:
        return h._bool(True)
    # ANSWERS rather than propagating: a missing attribute is False, not the
    # AttributeError the lookup raised.
    h.err = None
    h.err_value = None
    return h._bool(False)


def _apy_all(h, a):
    items = _seq_items(h, h._get(a[0], "apy_all"), "apy_all")
    if items is None:
        return 0
    return h._bool(all(bool(x) for x in items))


def _apy_any(h, a):
    items = _seq_items(h, h._get(a[0], "apy_any"), "apy_any")
    if items is None:
        return 0
    return h._bool(any(bool(x) for x in items))


_TABLE.update({
    "apy_ord": _apy_ord,
    "apy_chr": _apy_chr,
    "apy_callable": _apy_callable,
    "apy_hasattr": _apy_hasattr,
    "apy_all": _apy_all,
    "apy_any": _apy_any,
})


def _call_key(h, keyfn, item):
    """Apply a key function to one element, or return it unchanged."""
    if keyfn is None:
        return item
    return h._invoke(keyfn, [item])


def _apy_sorted_by(h, a):
    """`sorted(xs, key=f, reverse=r)`.

    Python's own `sorted` with a key computed once per element -- so the
    stability that `reverse=True` must preserve comes from CPython rather than
    from a restatement of it. Reversing the RESULT would reverse equal
    elements too, which is the subtle half.
    """
    items = _seq_items(h, h._get(a[0], "apy_sorted_by"), "apy_sorted_by")
    if items is None:
        return 0
    keyfn = h._get(a[1], "apy_sorted_by")
    keyfn = None if keyfn is None else keyfn
    reverse = bool(h._get(a[2], "apy_sorted_by"))
    try:
        if keyfn is None:
            return h._new(sorted(items, reverse=reverse))
        return h._new(sorted(items, key=lambda v: _call_key(h, keyfn, v),
                             reverse=reverse))
    except TypeError as exc:
        return h._fail_like(exc)


def _extreme_by(h, a, which, name):
    items = _seq_items(h, h._get(a[0], name), name)
    if items is None:
        return 0
    keyfn = h._get(a[1], name)
    try:
        if keyfn is None:
            return h._value(which(items))
        return h._value(which(items, key=lambda v: _call_key(h, keyfn, v)))
    except (TypeError, ValueError) as exc:
        return h._fail_like(exc)


def _apy_min_by(h, a):
    return _extreme_by(h, a, min, "apy_min_by")


def _apy_max_by(h, a):
    return _extreme_by(h, a, max, "apy_max_by")


_TABLE.update({
    "apy_sorted_by": _apy_sorted_by,
    "apy_min_by": _apy_min_by,
    "apy_max_by": _apy_max_by,
})


class Iterator:
    """A cursor over something indexable -- what `iter(x)` returns.

    Not a Python iterator: the compiled runtime walks by INDEX, so an iterator
    there is a container plus a position, and reproducing that exactly is what
    keeps the two paths agreeing about a half-consumed one.
    """

    __slots__ = ("src", "i", "fn", "mode")

    #: WHAT A CURSOR DOES on the way. A plain one walks; the rest apply
    #: something as they go, which is what makes `map(f, xs)` lazy -- `f` runs
    #: when the result is walked, not when it is made.
    PLAIN, MAP, FILTER, ENUMERATE, ZIP = range(5)

    def __init__(self, src, fn=None, mode: int = 0, start: int = 0) -> None:
        self.src = src
        self.fn = fn
        self.mode = mode
        self.i = start


def _apy_iterable(h, a):
    """WHAT TO WALK. `v` itself for anything indexable, and for a user object
    the iterator protocol DRAINED into a list.

    Eager where CPython is lazy, which is visible: a `for` over an infinite
    `__next__` never starts rather than never ending. Laziness needs a
    resumable frame -- the same thing `yield` needs.
    """
    v = h._get(a[0], "apy_iterable")
    if isinstance(v, Gen):
        # DRAINED: the walk below is by index and an index walk needs a
        # length. See `_apy_gen_drain` for what that costs.
        return _apy_gen_drain(h, a)
    if not isinstance(v, Instance):
        return a[0]
    try:
        got = v._send("__iter__")
    except _UserFailed:
        return 0
    if h.err is not None:
        return 0
    if got is NotImplemented:
        # No `__iter__`. `__len__` plus `__getitem__` is the older protocol
        # and the index walk already IS it; `__getitem__` alone is walked
        # until it reports IndexError, which is how CPython ends that one.
        if v.cls.find("__len__") is not None:
            return a[0]
        if v.cls.find("__getitem__") is None:
            return h._fail("TypeError",
                           f"'{h.kind_name(v)}' object is not iterable")
        out = []
        for i in range(1000000):
            try:
                item = v._send("__getitem__", i)
            except _UserFailed:
                item = None
            if h.err is not None:
                if "IndexError" in _exc_chain(h, h.err[0]):
                    h.err = None
                    h.err_value = None
                    break
                return 0
            out.append(item)
        return h._new(out)
    if not isinstance(got, Instance) or got.cls.find("__next__") is None:
        return _apy_iterable(h, [h._value(got)])
    out = []
    for i in range(1000000):
        try:
            item = got._send("__next__")
        except _UserFailed:
            item = None
        if h.err is not None:
            if "StopIteration" in _exc_chain(h, h.err[0]):
                h.err = None
                h.err_value = None
                break
            return 0
        out.append(item)
    return h._new(out)


class Gen:
    """A SUSPENDED FUNCTION -- what a `def` containing `yield` builds.

    Its locals live here rather than in registers, because a register does not
    survive the return a `yield` compiles to. `state` is 0 before the first
    step, k while suspended at yield k, and -1 once the body has finished.
    See `apy_gen_new` in link/objects.py for the whole shape.
    """

    __slots__ = ("step", "slots", "state", "sent", "running", "cache",
                 "pending", "result")

    def __init__(self, step, nslots: int) -> None:
        self.step = step
        self.slots = [None] * nslots
        self.state = 0
        self.sent = None
        self.running = False
        #: What a LENGTH QUERY drained. `sum(g)` walks by index and an index
        #: walk needs a length, so the first one to ask consumes the generator
        #: and every later index reads from the same list.
        self.cache = None
        #: An exception to raise AT THE SUSPENSION POINT, from `throw` or
        #: `close`. Delivered by the resume block, so a `try` inside the body
        #: catches it -- the whole difference between `throw` and raising at
        #: the call site.
        self.pending = None
        #: WHAT `return` GAVE, waiting to become `StopIteration.value`. A
        #: generator's return value is not the call's result -- the call
        #: already returned the generator -- so this is the only place it can
        #: live between the `return` and the exception that carries it.
        self.result = None


def _apy_gen_new(h, a):
    return h._new(Gen(h._get(a[0], "apy_gen_new"), int(a[1])))


def _apy_gen_slot(h, a):
    g = h._get(a[0], "apy_gen_slot")
    i = int(a[1])
    # An UNSET slot reads as None rather than as a null: a local a `yield` has
    # not reached yet is not an error to read, and a null would be taken for a
    # pending exception by the next operation that touched it.
    return h._value(g.slots[i] if 0 <= i < len(g.slots) else None)


def _apy_gen_set(h, a):
    g = h._get(a[0], "apy_gen_set")
    i = int(a[1])
    if 0 <= i < len(g.slots):
        g.slots[i] = h._get(a[2], "apy_gen_set")
    return h._none


def _apy_gen_state(h, a):
    return h._get(a[0], "apy_gen_state").state


def _apy_gen_iget(h, a):
    """A slot holding a RAW machine word -- a `for` index that must survive a
    yield, kept unboxed because boxing it would allocate per iteration."""
    g = h._get(a[0], "apy_gen_iget")
    i = int(a[1])
    got = g.slots[i] if 0 <= i < len(g.slots) else 0
    return int(got) if isinstance(got, int) else 0


def _apy_gen_iset(h, a):
    g = h._get(a[0], "apy_gen_iset")
    i = int(a[1])
    if 0 <= i < len(g.slots):
        g.slots[i] = int(a[2])
    return h._none


def _apy_gen_taken(h, a):
    """What a delegated generator RETURNED, for `yield from` to answer with.

    Read off the object rather than caught as a StopIteration: the delegation
    drains rather than stepping, so there is no exception left to catch.
    """
    g = h._get(a[0], "apy_gen_taken")
    return h._value(g.result if isinstance(g, Gen) else None)


def _apy_gen_result(h, a):
    """`return v` inside the body, held until exhaustion is reported."""
    h._get(a[0], "apy_gen_result").result = h._get(a[1], "apy_gen_result")
    return h._none


def _gen_stop(h, g):
    """The exhaustion signal, carrying whatever `return` gave. A generator
    that returned nothing raises a bare StopIteration, which is not the same
    as one that returned None."""
    if g.result is None:
        return h._fail("StopIteration", "")
    return h._fail_raised(Exc("StopIteration", g.result))


def _apy_gen_goto(h, a):
    h._get(a[0], "apy_gen_goto").state = int(a[1])
    return h._none


def _apy_gen_sent(h, a):
    return h._value(h._get(a[0], "apy_gen_sent").sent)


def _apy_gen_throwing(h, a):
    """Is an exception waiting to be raised here? Asked by every resume
    block."""
    return 1 if h._get(a[0], "apy_gen_throwing").pending is not None else 0


def _apy_gen_pending(h, a):
    g = h._get(a[0], "apy_gen_pending")
    got, g.pending = g.pending, None      # CLEARING, so it fires once
    return h._value(got)


def _gen_step(h, g, sent):
    """One step. Returns (value, done) or (None, None) on failure.

    A generator ALREADY RUNNING cannot be re-entered: `next(g)` from inside
    `g` is a ValueError, and without the guard it would corrupt the slots it
    is halfway through writing.
    """
    if not isinstance(g, Gen):
        h._fail("TypeError", f"'{h.kind_name(g)}' object is not a generator")
        return None, None
    if g.running:
        h._fail("ValueError", "generator already executing")
        return None, None
    if g.state < 0:
        return None, True
    g.sent = sent
    g.running = True
    try:
        out = h._invoke_obj(g.step, [g])
    except _UserFailed:
        g.state = -1
        return None, None
    finally:
        g.running = False
    # The body sets the state to -1 on its way out, so "did this call finish
    # the generator" is a question about the state AFTER it, not about the
    # value -- a generator may legitimately yield None.
    return out, g.state < 0


def _apy_gen_next(h, a):
    value, done = _gen_step(h, h._get(a[0], "apy_gen_next"), None)
    if done is None:
        return 0
    if done:
        if int(a[2]):
            return a[1]
        return _gen_stop(h, h._get(a[0], "apy_gen_next"))
    return h._value(value)


def _apy_gen_send(h, a):
    g = h._get(a[0], "apy_gen_send")
    v = h._get(a[1], "apy_gen_send")
    if isinstance(g, Gen) and g.state == 0 and v is not None:
        return h._fail("TypeError", "can't send non-None value to a "
                                    "just-started generator")
    value, done = _gen_step(h, g, v)
    if done is None:
        return 0
    if done:
        return _gen_stop(h, g)
    return h._value(value)


def _apy_gen_throw(h, a):
    """`g.throw(exc)` -- raise AT the suspension point, so a `try` around the
    `yield` inside the body catches it."""
    g = h._get(a[0], "apy_gen_throw")
    exc = h._get(a[1], "apy_gen_throw")
    if not isinstance(g, Gen):
        return h._fail("AttributeError",
                       f"'{h.kind_name(g)}' object has no attribute 'throw'")
    # NOT YET STARTED, or already finished: there is no suspension point to
    # raise at, so it is raised here.
    if g.state <= 0:
        g.state = -1
        return _apy_raise(h, [a[1]])
    g.pending = exc
    value, done = _gen_step(h, g, None)
    if done is None:
        return 0
    if done:
        return h._fail("StopIteration", "")
    return h._value(value)


def _apy_gen_close(h, a):
    """A GeneratorExit at the suspension point, so a `finally` in the body
    runs. The exception is SWALLOWED if it comes back out, which is what makes
    `close` quiet; anything else the body raised propagates."""
    g = h._get(a[0], "apy_gen_close")
    if not isinstance(g, Gen):
        return h._fail("AttributeError",
                       f"'{h.kind_name(g)}' object has no attribute 'close'")
    if g.state > 0:
        g.pending = Exc("GeneratorExit", None, has_arg=False)
        _gen_step(h, g, None)
        if h.err is not None:
            if "GeneratorExit" in _exc_chain(h, h.err[0]):
                h.err = None
                h.err_value = None
            else:
                g.state = -1
                return 0
    g.state = -1
    return h._none


def _apy_gen_drain(h, a):
    """Everything a generator will yield, as a list. EAGER, where CPython is
    lazy -- see `apy_gen_drain`."""
    g = h._get(a[0], "apy_gen_drain")
    out = []
    for _ in range(1000000):
        value, done = _gen_step(h, g, None)
        if done is None:
            return 0
        if done:
            break
        out.append(value)
    return h._new(out)


#: The exhaustion SENTINEL -- a cell, not a null, because null already means
#: "an error is set" and running out is not an error. One object, so the test
#: is an identity compare.
_STOP = object()


def _apy_stop(h, a):
    return h._stop


def _apy_getiter(h, a):
    """What to step. See `apy_getiter` for why iteration advances rather than
    walking by index."""
    v = h._get(a[0], "apy_getiter")
    if isinstance(v, (Gen, Iterator)):
        return a[0]               # a generator IS its own cursor
    if isinstance(v, Instance):
        try:
            got = v._send("__iter__")
        except _UserFailed:
            return 0
        if h.err is not None:
            return 0
        if got is not NotImplemented:
            if isinstance(got, Instance) and got.cls.find("__next__") is not None:
                return h._value(got)
            return _apy_getiter(h, [h._value(got)])
        if v.cls.find("__getitem__") is None:
            return h._fail("TypeError",
                           f"'{h.kind_name(v)}' object is not iterable")
    elif not isinstance(v, (list, tuple, set, frozenset, dict, str, bytes)):
        return h._fail("TypeError",
                       f"'{h.kind_name(v)}' object is not iterable")
    return h._new(Iterator(v))


def _apy_step(h, a):
    it = h._get(a[0], "apy_step")
    if isinstance(it, Iterator) and it.mode != Iterator.PLAIN:
        return _step_cursor(h, it)
    if isinstance(it, Gen):
        value, done = _gen_step(h, it, None)
        if done is None:
            return 0
        return h._stop if done else h._value(value)
    if isinstance(it, Instance):
        # A user iterator: `__next__` until StopIteration, which is the
        # protocol rather than a sentinel here.
        try:
            got = it._send("__next__")
        except _UserFailed:
            got = NotImplemented
        if h.err is not None:
            if "StopIteration" in _exc_chain(h, h.err[0]):
                h.err = None
                h.err_value = None
                return h._stop
            return 0
        if got is NotImplemented:
            return h._fail("TypeError",
                           f"'{h.kind_name(it)}' object is not an iterator")
        return h._value(got)
    if not isinstance(it, Iterator):
        return h._fail("TypeError",
                       f"'{h.kind_name(it)}' object is not an iterator")
    src = it.src
    if isinstance(src, Instance):
        # Walked through `__getitem__`, ending on the IndexError the class
        # raises -- CPython's rule for the older protocol.
        try:
            got = src._send("__getitem__", it.i)
        except _UserFailed:
            got = None
        if h.err is not None:
            if "IndexError" in _exc_chain(h, h.err[0]):
                h.err = None
                h.err_value = None
                return h._stop
            return 0
        it.i += 1
        return h._value(got)
    # THE LENGTH IS READ EVERY STEP, which is the point: a body that appends
    # to the list it is walking sees the new elements.
    items = list(src)
    if it.i >= len(items):
        return h._stop
    got = items[it.i]
    it.i += 1
    return h._value(got)


def _step_cursor(h, it):
    """One step of a cursor that TRANSFORMS as it goes."""
    def pull(src):
        return h._get(_apy_step(h, [h._value(src)]), "apy_step")

    if it.mode == Iterator.MAP:
        v = pull(it.src)
        if v is _STOP or h.err is not None:
            return 0 if h.err is not None else h._stop
        return _user(h, lambda: h._value(h._invoke(it.fn, [v])))
    if it.mode == Iterator.FILTER:
        for _ in range(100000000):
            v = pull(it.src)
            if h.err is not None:
                return 0
            if v is _STOP:
                return h._stop
            # `filter(None, xs)` keeps the truthy elements -- a real form, and
            # why the callable is TESTED rather than simply called.
            if it.fn is None:
                keep = v
            else:
                try:
                    keep = h._invoke(it.fn, [v])
                except _UserFailed:
                    return 0
            if keep:
                return h._value(v)
        return h._stop
    if it.mode == Iterator.ENUMERATE:
        v = pull(it.src)
        if h.err is not None:
            return 0
        if v is _STOP:
            return h._stop
        at, it.i = it.i, it.i + 1
        return h._new((at, v))
    # zip. `zip()` with no arguments is EMPTY, not endless.
    if not it.src:
        return h._stop
    row = []
    for k, cur in enumerate(it.src):
        v = pull(cur)
        if h.err is not None:
            return 0
        if v is _STOP:
            # STOPS AT THE SHORTEST, which is what makes zip lossy; `strict`
            # reports instead.
            if it.fn and k > 0:
                return h._fail("ValueError",
                               "zip() argument 2 is shorter than argument 1")
            return h._stop
        row.append(v)
    return h._new(tuple(row))


def _drain_cursor(h, it):
    """Walk a cursor to the end and BECOME a plain one over what it produced.

    Asking a lazy thing for its length is asking it to run, so it runs once
    and keeps the result -- a length query followed by an index walk sees the
    same elements. What is consumed stays consumed.
    """
    out = []
    for _ in range(100000000):
        got = _apy_step(h, [h._value(it)])
        if not got:
            return None
        v = h._get(got, "apy_step")
        if v is _STOP:
            break
        out.append(v)
    it.src, it.fn, it.mode, it.i = out, None, Iterator.PLAIN, 0
    return out


def _apy_iter(h, a):
    v = h._get(a[0], "apy_iter")
    if isinstance(v, Iterator):
        return h._new(v)          # `iter(it)` is `it`
    if isinstance(v, Gen):
        # `iter(g)` IS `g`, so a half-consumed generator keeps its position.
        return a[0]
    if isinstance(v, Instance):
        # `iter(obj)` answers what `__iter__` did, UNCHANGED, so that
        # `iter(it) is it` holds for a class that returns self.
        got = v._send("__iter__")
        if h.err is not None:
            return 0
        if got is not NotImplemented:
            return h._value(got)
        drained = _apy_iterable(h, a)
        if not drained:
            return 0
        return _apy_iter(h, [drained])
    if not isinstance(v, (list, tuple, set, frozenset, dict, str, bytes)):
        return h._fail("TypeError",
                       f"'{h.kind_name(v)}' object is not iterable")
    return h._new(Iterator(v))


def _iter_items(v):
    """What an iterator's source yields, in order."""
    return list(v) if not isinstance(v, dict) else list(v)


def _apy_next(h, a):
    """ONE STEP, through the same protocol `for` uses.

    Anything with a position -- a generator, a cursor, a user iterator --
    advances the same way, so `next(map(f, xs))` calls `f` once rather than
    draining. Exhaustion is a StopIteration here and a sentinel there, which
    is the only difference between the two spellings.
    """
    it = h._get(a[0], "apy_next")
    if not isinstance(it, (Gen, Iterator, Instance)):
        return h._fail("TypeError",
                       f"'{h.kind_name(it)}' object is not an iterator")
    got = _apy_step(h, [a[0]])
    if not got:
        return 0
    if h._get(got, "apy_next") is _STOP:
        return a[1] if int(a[2]) else h._fail("StopIteration", "")
    return got


def _apy_map(h, a):
    """`map(f, xs)` -- LAZY. `f` runs when the result is walked, not when it
    is made, and a program with a side-effecting `f` can tell."""
    src = _apy_getiter(h, [a[1]])
    if not src:
        return 0
    return h._new(Iterator(h._get(src, "apy_map"),
                           h._get(a[0], "apy_map"), Iterator.MAP))


def _apy_filter(h, a):
    src = _apy_getiter(h, [a[1]])
    if not src:
        return 0
    return h._new(Iterator(h._get(src, "apy_filter"),
                           h._get(a[0], "apy_filter"), Iterator.FILTER))


def _apy_print_seq(h, a):
    """`print` reached through a VALUE. The arguments arrive as a tuple
    because a value-form has no compile-time count."""
    items = h._get(a[0], "apy_print_seq")
    h._interp._emit(" ".join(h._text(v, False) for v in items) + "\n")
    return h._none


def _apy_dict_of(h, a):
    items = list(h._get(a[0], "apy_dict_of"))
    if not items:
        return h._new({})
    return _apy_to_dict(h, [h._value(items[0])])


def _apy_bytes_of(h, a):
    items = list(h._get(a[0], "apy_bytes_of"))
    if not items:
        return h._new(b"")
    return _apy_to_bytes(h, [h._value(items[0])])


def _apy_dict_fromkeys(h, a):
    """`dict.fromkeys(keys, value)` -- every key mapped to the SAME value.

    The sharing is the point and the trap: `dict.fromkeys(ks, [])` gives every
    key the same list, and appending through one key is visible through all.
    """
    keys = _seq_items(h, h._get(a[0], "apy_dict_fromkeys"), "apy_dict_fromkeys")
    if keys is None:
        return 0
    value = h._get(a[1], "apy_dict_fromkeys")
    out = {}
    for key in keys:
        if _dict_set(h, out, key, value) == 0:
            return 0
    return h._new(out)


def _apy_from_bytes_n(h, a):
    """`int.from_bytes(b, byteorder)`, unsigned -- the signed form takes a
    keyword this does not offer, and guessing would turn a large positive
    number negative."""
    b = h._get(a[0], "apy_from_bytes_n")
    order = h._get(a[1], "apy_from_bytes_n")
    if not isinstance(b, bytes):
        return h._fail("TypeError",
                       f"cannot convert '{h.kind_name(b)}' object to bytes")
    if len(b) > 8:
        return h._fail("OverflowError", "int too big to convert")
    return h._int(int.from_bytes(b, "little" if order == "little" else "big"))


def _apy_to_dict(h, a):
    src = h._get(a[0], "apy_to_dict")
    # A COPY, not the same dict: `dict(d)` is a constructor and the result is
    # a new object, which only became visible once `|=` mutated in place.
    if isinstance(src, dict):
        return h._new(dict(src))
    items = _seq_items(h, src, "apy_to_dict")
    if items is None:
        return 0
    out = {}
    for i, pair in enumerate(items):
        # TWO DIFFERENT ERRORS, and Python distinguishes them: an element that
        # is not a sequence at all is a TypeError -- `dict([3, 1])` cannot
        # convert an int to a pair -- while one that IS a sequence of the
        # wrong length is a ValueError.
        # A STR IS A SEQUENCE HERE. `dict(['ab', 'cd'])` is
        # `{'a': 'b', 'c': 'd'}`, so the length check applies to it and the
        # TypeError does not.
        if not isinstance(pair, (list, tuple, str, bytes)):
            return h._fail("TypeError",
                           f"cannot convert dictionary update sequence "
                           f"element #{i} to a sequence")
        if len(pair) != 2:
            return h._fail("ValueError",
                           f"dictionary update sequence element #{i} has "
                           f"length {len(pair)}; 2 is required")
        out[pair[0]] = pair[1]
    return h._new(out)


def _apy_to_bytes(h, a):
    src = h._get(a[0], "apy_to_bytes")
    if isinstance(src, bytes):
        return h._new(src)
    if isinstance(src, str):
        return h._fail("TypeError", "string argument without an encoding")
    items = _seq_items(h, src, "apy_to_bytes")
    if items is None:
        return 0
    out = bytearray()
    for item in items:
        value = int(item)
        if not 0 <= value <= 255:
            return h._fail("ValueError", "bytes must be in range(0, 256)")
        out.append(value)
    return h._new(bytes(out))


_TABLE.update({
    "apy_gen_new": _apy_gen_new,
    "apy_gen_slot": _apy_gen_slot,
    "apy_gen_set": _apy_gen_set,
    "apy_gen_state": _apy_gen_state,
    "apy_gen_iget": _apy_gen_iget,
    "apy_gen_iset": _apy_gen_iset,
    "apy_gen_result": _apy_gen_result,
    "apy_gen_taken": _apy_gen_taken,
    "apy_gen_goto": _apy_gen_goto,
    "apy_gen_sent": _apy_gen_sent,
    "apy_gen_next": _apy_gen_next,
    "apy_gen_send": _apy_gen_send,
    "apy_gen_throwing": _apy_gen_throwing,
    "apy_gen_pending": _apy_gen_pending,
    "apy_gen_throw": _apy_gen_throw,
    "apy_gen_close": _apy_gen_close,
    "apy_gen_drain": _apy_gen_drain,
    "apy_stop": _apy_stop,
    "apy_getiter": _apy_getiter,
    "apy_step": _apy_step,
    "apy_iter": _apy_iter,
    "apy_iterable": _apy_iterable,
    "apy_next": _apy_next,
    "apy_map": _apy_map,
    "apy_filter": _apy_filter,
    "apy_print_seq": _apy_print_seq,
    "apy_dict_of": _apy_dict_of,
    "apy_bytes_of": _apy_bytes_of,
    "apy_dict_fromkeys": _apy_dict_fromkeys,
    "apy_from_bytes_n": _apy_from_bytes_n,
    "apy_to_dict": _apy_to_dict,
    "apy_to_bytes": _apy_to_bytes,
})


def _apy_set_update(h, a):
    """`{*xs, y}` -- every element of a sequence ADDED to a set.

    `extend` appends and would let a duplicate through; that distinction is
    the whole difference between a set display and a list one.
    """
    target = h._get(a[0], "apy_set_update")
    src = h._get(a[1], "apy_set_update")
    if not isinstance(target, set):
        return h._fail("AttributeError",
                       f"'{h.kind_name(target)}' object has no attribute "
                       f"'update'")
    items = _seq_items(h, src, "apy_set_update")
    if items is None:
        return 0
    for item in items:
        try:
            hash(item)
        except TypeError as exc:
            return h._fail_like(exc)
        target.add(item)
    return h._none


_TABLE["apy_set_update"] = _apy_set_update
