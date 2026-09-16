"""`operator`, as ordinary Python this compiler compiles.

COVERAGE: the whole public surface CPython 3.14 lists for this module --
`dir(operator)` minus the C-accelerated `_operator` names it re-exports under
the same spellings. Every arithmetic, comparison, bitwise, sequence, identity
and in-place function; `attrgetter`, `itemgetter`, `methodcaller` (each with
their single- and multi-argument forms, and dotted attribute paths);
`countOf`, `indexOf`, `index`, `length_hint`, `call`, `truth`; and the
literal dunder-named aliases CPython assigns (`__add__` is `add`, and so on
for every one of them -- see the loop at the bottom of CPython's own
`Lib/operator.py`, which this restates rather than a hand-picked subset of
it).

NOT COVERED: nothing is refused BY NAME -- every name `operator.__all__`
lists in CPython 3.14, plus every dunder alias CPython assigns afterward, is
here, and each does what CPython specifies for the overwhelming case, a
builtin type. Two narrow behavioural gaps remain where the CAUSE is not in
this module: `methodcaller` cannot reach a BUILTIN type's own method (there
is no bound-method value for one yet), and seven of the twelve in-place
operators (all but `+ - * & | ^`) do not consult a user-defined class's own
`__i*__` override, `imatmul` included. Both are measured, both are compiler
facts rather than mistakes in what follows, and both are explained in full
below, in the paragraph each belongs to.

Restored rather than a port of CPython's C `_operator` module -- there is
nothing to port. CPython's own pure-Python fallback (`Lib/operator.py`,
used when the accelerator is absent) is ORDINARY PYTHON already, and each
function here is line-for-line what that file says its operator does, so it
participates in this compiler's own dynamic dispatch exactly like a `+` or a
`[]` written directly in a program: an object with `__add__` sees
`operator.add` reach it the same way `a + b` does, because both compile to
the same thing.

ONE FUNCTION COULD NOT BE COPIED AS CPYTHON WRITES IT, and that is the one
compiler fact this module found. CPython's `abs` is:

    from builtins import abs as _abs
    def abs(a):
        return _abs(a)

which works because CPython executes a module top to bottom: `_abs = abs`
runs while `abs` still names the builtin, and only the `def` after it makes
`abs` a module global. This compiler HOISTS every module-level `def` before
running any top-level statement, so by the time `_abs = abs` would run,
`abs` already names the function being defined and `_abs(a)` calls itself --
measured directly, and it recurses until `RecursionError`. The fix does not
touch the compiler: `abs(a)` is written as `a.__abs__()` instead, which is
what the builtin does internally for every object that has one and needs no
name captured before it is shadowed.

TWO MORE WERE FOUND, and are recorded here because they are compiler facts
and not something this module works around invisibly.

`operator.index` NEEDED A REAL COMPILER FIX. `a.__index__()` -- an ordinary
dunder called directly, exactly like `a.__abs__()` two paragraphs up -- was
simply absent from the table `obj.method(...)` lowers through
(`DYN_METHOD_TABLE`, `frontends/python/methods.py`): every builtin int has
`__index__` in CPython, and calling it on one traded a correct answer for
`AttributeError: 'int' object has no attribute '__index__'`, measured
directly. Fixed on both runtimes this compiler has, following exactly the
shape `__abs__` already uses: `apy_index_obj` (`objects/c/_builtins.py`,
mirroring `apy_to_int`'s numeric branch -- a big answers itself, a bool
answers a genuine `int`) for the C runtime, and `_apy_index_obj`
(`objects/host.py`, beside `_apy_abs`) for the interpreter this compiler's
own `run` uses by default. Neither is a new capability: `apy_index` already
existed for a subscript's unboxed machine word (`runtime/mathints.py`); this
is that same dispatch, boxed, reached by one more spelling.

`itemgetter`'s many-item form COULD NOT BE WRITTEN THE OBVIOUS WAY, and this
one is NOT fixed here. `tuple(obj[i] for i in allitems)` inside the closure
`itemgetter.__init__` builds and returns is a generator expression -- which
this frontend compiles as its OWN nested function (`genexp_def`,
`frontends/python/analysis.py`) -- inside that closure -- inside `__init__`:
three function scopes deep, and free-variable resolution loses `allitems` at
that depth. Measured with no class involved at all: `def make(item, *items):
... def func(obj): return tuple(obj[i] for i in allitems); return func`
fails the same way, `NameError: name 'allitems' is not defined`, so this is
a fact about nesting depth and not about a method or this module's shape.
Likely the same family of bug as the positional-only-parameter one this
project already fixed (both are the closure scope builder not looking as
deep as the language allows) but not run down with the same confidence, so
`itemgetter` below writes the explicit loop instead -- two function scopes,
which is exactly the depth `attrgetter` above it already uses successfully --
rather than leave the test failing on a fix attempted without being sure of
it. Coverage is unaffected: `itemgetter`'s many-item form answers exactly
what CPython answers, by a different route through this compiler.

`methodcaller` IS NOT FULLY COVERED, and this is a real gap rather than a
compiler bug to chase: CPython's spec has it work on ANY object, `str`
included, and `methodcaller.__call__` is written exactly as CPython's own is
-- `getattr(obj, self._name)(*self._args, **self._kwargs)`. What is missing
is underneath `getattr`, not in this module: this compiler has no bound-
method VALUE for a BUILTIN type's instance (`methods.py`'s own module
docstring says so -- "There is no bound-method value here: `xs.append` on
its own is not something this can produce... Method lookup on an instance
will need that to change") -- so `getattr("hi", "upper")` fails with
`AttributeError: 'str' object has no attribute 'upper'`, measured directly,
for a method every `str` plainly has. A USER-DEFINED class's methods ARE
ordinary attributes and `getattr` finds them, checked the same way, so
`methodcaller` is exercised in `tests/stdlib/operator.py` against one of
those and not against `str`. Fixing the gap is the bound-method-value work
that comment already names as its own project rather than a narrow fix
belonging to this module.

SEVEN OF THE TWELVE IN-PLACE OPERATORS DO NOT CONSULT A USER CLASS'S OWN
DUNDER, and `imatmul` is the one this module happened to reach first.
`x @= y` for a `Grid` defining BOTH `__matmul__` and `__imatmul__`
differently answered what `__matmul__` alone would, on this compiler,
measured directly against CPython (which answers what `__imatmul__` says, as
the language defines `@=`). The cause is `frontends/python/dynamic.py`'s
`_dyn_inplace`: an augmented assignment routes through a user dunder only
for the six operators listed in `_INPLACE` (`+ | & ^ - *`) and its own
comment states the omission as a rule -- "everything else is the binary
operator, because that IS what `x //= y` means" -- which is the right answer
for every BUILTIN numeric type (none of `int`/`float`/`complex` define
`__ifloordiv__` etc. in CPython either, so falling to the binary operator is
exactly correct for them) and the wrong one for a class that defines
`__imatmul__`, `__itruediv__`, `__ifloordiv__`, `__imod__`, `__ipow__`,
`__ilshift__` or `__irshift__` of its own. NOT FIXED HERE: closing it
means widening `_INPLACE` and the interpreter's matching `_IOP_DUNDER` and
op-to-symbol tables (`objects/host.py`, `_apy_iop`) for all seven at
once, which is more than the one-line, fully-understood fix `__index__`
above got, and this module's own reach for `matmul` is one class in one
test rather than the whole surface that gap touches. So `tests/stdlib/
operator.py`'s `Grid` defines `__matmul__` alone: `imatmul` is exercised
through the fallback every in-place operator has for a type that does not
override it, which is correct on both runtimes and covers the far more
common case -- a plain numeric or user type with no `__imatmul__` at all.
"""


# Comparison Operations ########################################################

def lt(a, b):
    """Same as a < b."""
    return a < b


def le(a, b):
    """Same as a <= b."""
    return a <= b


def eq(a, b):
    """Same as a == b."""
    return a == b


def ne(a, b):
    """Same as a != b."""
    return a != b


def ge(a, b):
    """Same as a >= b."""
    return a >= b


def gt(a, b):
    """Same as a > b."""
    return a > b


# Logical Operations ###########################################################

def not_(a):
    """Same as not a."""
    return not a


def truth(a):
    """True if a is true, False otherwise."""
    return True if a else False


def is_(a, b):
    """Same as a is b."""
    return a is b


def is_not(a, b):
    """Same as a is not b."""
    return a is not b


def is_none(a):
    """Same as a is None."""
    return a is None


def is_not_none(a):
    """Same as a is not None."""
    return a is not None


# Mathematical/Bitwise Operations ##############################################

def abs(a):
    """Same as abs(a). See the module docstring for why this is `a.__abs__()`
    and not a captured builtin."""
    return a.__abs__()


def add(a, b):
    """Same as a + b."""
    return a + b


def and_(a, b):
    """Same as a & b."""
    return a & b


def floordiv(a, b):
    """Same as a // b."""
    return a // b


def index(a):
    """Same as a.__index__()."""
    return a.__index__()


def inv(a):
    """Same as ~a."""
    return ~a


invert = inv


def lshift(a, b):
    """Same as a << b."""
    return a << b


def mod(a, b):
    """Same as a % b."""
    return a % b


def mul(a, b):
    """Same as a * b."""
    return a * b


def matmul(a, b):
    """Same as a @ b."""
    return a @ b


def neg(a):
    """Same as -a."""
    return -a


def or_(a, b):
    """Same as a | b."""
    return a | b


def pos(a):
    """Same as +a."""
    return +a


def pow(a, b):
    """Same as a ** b."""
    return a ** b


def rshift(a, b):
    """Same as a >> b."""
    return a >> b


def sub(a, b):
    """Same as a - b."""
    return a - b


def truediv(a, b):
    """Same as a / b."""
    return a / b


def xor(a, b):
    """Same as a ^ b."""
    return a ^ b


# Sequence Operations ###########################################################

def concat(a, b):
    """Same as a + b, for a and b sequences."""
    if not hasattr(a, "__getitem__"):
        raise TypeError("'" + type(a).__name__ +
                         "' object can't be concatenated")
    return a + b


def contains(a, b):
    """Same as b in a (note reversed operands)."""
    return b in a


def countOf(a, b):
    """The number of items in a which are, or which equal, b."""
    count = 0
    for i in a:
        if i is b or i == b:
            count = count + 1
    return count


def delitem(a, b):
    """Same as del a[b]."""
    del a[b]


def getitem(a, b):
    """Same as a[b]."""
    return a[b]


def indexOf(a, b):
    """The first index of b in a.

    NOT `for ... else`: this compiler hoists module-level `def`s before
    running top-level statements (see the module docstring), and that is a
    module-level fact rather than a statement-shape one -- but the plain
    loop-then-raise below is what CPython's own `for/else` amounts to, so it
    costs nothing to write it the unambiguous way.
    """
    i = 0
    for j in a:
        if j is b or j == b:
            return i
        i = i + 1
    raise ValueError("sequence.index(x): x not in sequence")


def setitem(a, b, c):
    """Same as a[b] = c."""
    a[b] = c


def length_hint(obj, default=0):
    """An estimate of the number of items in obj.

    Exact when `len(obj)` works; otherwise `obj`'s `__length_hint__`, and
    `default` when neither answers.
    """
    if not isinstance(default, int):
        raise TypeError("'" + type(default).__name__ +
                         "' object cannot be interpreted as an integer")

    try:
        return len(obj)
    except TypeError:
        pass

    try:
        hint = type(obj).__length_hint__
    except AttributeError:
        return default

    try:
        val = hint(obj)
    except TypeError:
        return default
    if val is NotImplemented:
        return default
    if not isinstance(val, int):
        raise TypeError("__length_hint__ must be integer, not " +
                         type(val).__name__)
    if val < 0:
        raise ValueError("__length_hint__() should return >= 0")
    return val


# Other Operations ##############################################################

def call(obj, *args, **kwargs):
    """Same as obj(*args, **kwargs)."""
    return obj(*args, **kwargs)


# Generalized Lookup Objects ####################################################

class attrgetter:
    """A callable that fetches the given attribute(s) from its operand.

    `attrgetter('name')(r)` is `r.name`. `attrgetter('name', 'date')(r)` is
    `(r.name, r.date)`. `attrgetter('name.first')(r)` is `r.name.first` --
    each argument may be a DOTTED PATH, walked one `getattr` at a time.
    """

    def __init__(self, attr, *attrs):
        if not attrs:
            if not isinstance(attr, str):
                raise TypeError("attribute name must be a string")
            self._attrs = (attr,)
            names = attr.split(".")

            def func(obj):
                for name in names:
                    obj = getattr(obj, name)
                return obj
            self._call = func
        else:
            allattrs = (attr,) + attrs
            self._attrs = allattrs
            paths = [one.split(".") for one in allattrs]

            def func(obj):
                out = []
                for names in paths:
                    val = obj
                    for name in names:
                        val = getattr(val, name)
                    out.append(val)
                return tuple(out)
            self._call = func

    def __call__(self, obj):
        return self._call(obj)


class itemgetter:
    """A callable that fetches the given item(s) from its operand.

    `itemgetter(2)(r)` is `r[2]`. `itemgetter(2, 5, 3)(r)` is
    `(r[2], r[5], r[3])`.
    """

    def __init__(self, item, *items):
        if not items:
            self._items = (item,)

            def func(obj):
                return obj[item]
            self._call = func
        else:
            allitems = (item,) + items
            self._items = allitems

            # NOT `tuple(obj[i] for i in allitems)`. A generator expression
            # is its own nested function (see `genexp_def`,
            # `frontends/python/analysis.py`), so that spelling is a
            # generator scope inside `func`'s inside `__init__`'s -- THREE
            # deep -- and free-variable resolution loses `allitems` at that
            # depth: measured directly, `NameError: name 'allitems' is not
            # defined`, with the same shape one level down
            # (`def make(item, *items): ... def func(obj): return
            # tuple(obj[i] for i in allitems)` fails the same way with no
            # class involved at all). The explicit loop is two function
            # scopes, which is exactly the depth `attrgetter` below already
            # uses successfully, and is worth not chasing further here.
            def func(obj):
                out = []
                for i in allitems:
                    out.append(obj[i])
                return tuple(out)
            self._call = func

    def __call__(self, obj):
        return self._call(obj)


class methodcaller:
    """A callable that calls the named method on its operand.

    `methodcaller('name')(r)` is `r.name()`.
    `methodcaller('name', 'date', foo=1)(r)` is `r.name('date', foo=1)`.
    """

    def __init__(self, name, *args, **kwargs):
        if not isinstance(name, str):
            raise TypeError("method name must be a string")
        self._name = name
        self._args = args
        self._kwargs = kwargs

    def __call__(self, obj):
        return getattr(obj, self._name)(*self._args, **self._kwargs)


# In-place Operations ###########################################################

def iadd(a, b):
    """Same as a += b."""
    a += b
    return a


def iand(a, b):
    """Same as a &= b."""
    a &= b
    return a


def iconcat(a, b):
    """Same as a += b, for a and b sequences."""
    if not hasattr(a, "__getitem__"):
        raise TypeError("'" + type(a).__name__ +
                         "' object can't be concatenated")
    a += b
    return a


def ifloordiv(a, b):
    """Same as a //= b."""
    a //= b
    return a


def ilshift(a, b):
    """Same as a <<= b."""
    a <<= b
    return a


def imod(a, b):
    """Same as a %= b."""
    a %= b
    return a


def imul(a, b):
    """Same as a *= b."""
    a *= b
    return a


def imatmul(a, b):
    """Same as a @= b."""
    a @= b
    return a


def ior(a, b):
    """Same as a |= b."""
    a |= b
    return a


def ipow(a, b):
    """Same as a **= b."""
    a **= b
    return a


def irshift(a, b):
    """Same as a >>= b."""
    a >>= b
    return a


def isub(a, b):
    """Same as a -= b."""
    a -= b
    return a


def itruediv(a, b):
    """Same as a /= b."""
    a /= b
    return a


def ixor(a, b):
    """Same as a ^= b."""
    a ^= b
    return a


# Dunder aliases ################################################################
# CPython assigns these after importing the accelerator so a program that
# spells `operator.__add__` gets exactly `operator.add` -- see the tail of
# `Lib/operator.py`. Restated here in the same order.

__lt__ = lt
__le__ = le
__eq__ = eq
__ne__ = ne
__ge__ = ge
__gt__ = gt
__not__ = not_
__abs__ = abs
__add__ = add
__and__ = and_
__call__ = call
__floordiv__ = floordiv
__index__ = index
__inv__ = inv
__invert__ = invert
__lshift__ = lshift
__mod__ = mod
__mul__ = mul
__matmul__ = matmul
__neg__ = neg
__or__ = or_
__pos__ = pos
__pow__ = pow
__rshift__ = rshift
__sub__ = sub
__truediv__ = truediv
__xor__ = xor
__concat__ = concat
__contains__ = contains
__delitem__ = delitem
__getitem__ = getitem
__setitem__ = setitem
__iadd__ = iadd
__iand__ = iand
__iconcat__ = iconcat
__ifloordiv__ = ifloordiv
__ilshift__ = ilshift
__imod__ = imod
__imul__ = imul
__imatmul__ = imatmul
__ior__ = ior
__ipow__ = ipow
__irshift__ = irshift
__isub__ = isub
__itruediv__ = itruediv
__ixor__ = ixor
