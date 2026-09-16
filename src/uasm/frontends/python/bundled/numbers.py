"""Abstract Base Classes (ABCs) for numbers, according to PEP 3141.

COVERAGE: `Number`, `Complex`, `Real`, `Rational`, `Integral` -- the whole
module, structured exactly as CPython's own: `Complex(Number)`,
`Real(Complex)`, `Rational(Real)`, `Integral(Rational)`, every abstract
method CPython declares on each, and the same mixins (`__sub__`/`__rsub__`
from `__add__`/`__neg__`, `Real.__complex__`/`.real`/`.imag`/`.conjugate`
from `Complex`'s abstracts, `Rational.__float__` from `.numerator` and
`.denominator`, `Integral.__index__`/`.__float__`/`.numerator`/
`.denominator`). `int` registers with the numeric tower up through `Number`,
`float` likewise up through `Number`, matching what `isinstance(3, Number)`
and `isinstance(3.0, Number)` answer in CPython.

TWO THINGS DIVERGE FROM CPYTHON'S OWN SOURCE TEXT, both measured rather than
guessed, and both explained here because a reader diffing against the real
module will otherwise call them mistakes.

REGISTRATION HAS TO HAPPEN AT EVERY LEVEL, not only at the leaf the way
CPython writes `Integral.register(int)` and nothing else. CPython's C `_abc`
module walks a registered class's real MRO outward, so registering at
`Integral` -- a real subclass of `Rational`, `Real`, `Complex`, `Number` --
answers True for all five. `bundled/abc.py`'s `ABCMeta` walks the OTHER
direction when a registry lookup misses: `cls`'s own ancestors, not the
registries of `cls`'s descendants -- so a registration at the most specific
class does not reach the general ones. CHECKED DIRECTLY, since `abc.py` is
ordinary Python and needs no compiler to run: loading it under a plain
interpreter with only `Integral.register(int)` answers `True` for
`isinstance(3, Integral)` and `False` for `Rational`, `Real`, `Complex` and
`Number` -- CPython answers `True` for all five. `collections.abc` never hit
this because it registers `dict` at `Mapping`, `MutableMapping` AND
`Reversible` individually rather than relying on one registration to reach
the others; this module does the same, at every applicable level, for every
builtin it registers -- AND `bool` REGISTERS SEPARATELY FROM `int`, which
CPython does not have to do: `bool` is a real subclass there and `_abc`
walks its MRO, while a builtin type reached as a VALUE here has no `__mro__`
for `ABCMeta.__subclasscheck__` to walk.

`complex` IS REGISTERED WITH `Complex`, as the line at the bottom of this
file, matching CPython -- and it does not make `isinstance` or `issubclass`
answer correctly for an actual `complex` value, which is the one thing this
module cannot close. `complex` compiles as a bare name (it is not the
`E0056` refusal `collections.abc` documented for it), but the value it
becomes is a synthesised CALLABLE THUNK, not the class object `type(3+4j)`
answers with -- `_BUILTIN_TYPE_VALUES` in `frontends/python/dynamic.py` is
the list of builtin names whose bare-name value IS the canonical type object
(`int`, `float`, `bool`, `str`, `bytes`, `list`, `tuple`, `dict`, `set`,
`frozenset`), and `complex` is not on it. MEASURED: `type(3+4j) is complex`
answers `False` here, and `Foo.register(complex)` followed by
`isinstance(3+4j, Foo)` answers `False` where CPython answers `True`, for a
plain ABC built the same way this module's are. That is the same shape of
gap `collections.abc` found for `range`/`bytearray`/`memoryview` -- a
builtin that cannot travel as a real class value -- just reached through a
compiling-but-wrong path instead of a refusal, which is why it is spelled
out here rather than left for a diff to notice. `tests/stdlib/numbers.py`
does not assert `isinstance`/`issubclass` against an actual `complex` value
or the `complex` type for exactly this reason.
"""

from abc import ABCMeta, abstractmethod

__all__ = ["Number", "Complex", "Real", "Rational", "Integral"]


class Number(metaclass=ABCMeta):
    """All numbers inherit from this class.

    If you just want to check if an argument x is a number, without caring
    what kind, use isinstance(x, Number).
    """
    __slots__ = ()

    # Concrete numeric types must provide their own hash implementation
    __hash__ = None


class Complex(Number):
    """Complex defines the operations that work on the builtin complex type.

    In short, those are: a conversion to complex, .real, .imag, +, -, *, /,
    **, abs(), .conjugate, ==, and !=.
    """
    __slots__ = ()

    @abstractmethod
    def __complex__(self):
        """Return a builtin complex instance. Called for complex(self)."""
        raise NotImplementedError

    def __bool__(self):
        """True if self != 0. Called for bool(self)."""
        return self != 0

    @property
    @abstractmethod
    def real(self):
        """Retrieve the real component of this number.

        This should subclass Real.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def imag(self):
        """Retrieve the imaginary component of this number.

        This should subclass Real.
        """
        raise NotImplementedError

    @abstractmethod
    def __add__(self, other):
        """self + other"""
        raise NotImplementedError

    @abstractmethod
    def __radd__(self, other):
        """other + self"""
        raise NotImplementedError

    @abstractmethod
    def __neg__(self):
        """-self"""
        raise NotImplementedError

    @abstractmethod
    def __pos__(self):
        """+self"""
        raise NotImplementedError

    def __sub__(self, other):
        """self - other"""
        return self + -other

    def __rsub__(self, other):
        """other - self"""
        return -self + other

    @abstractmethod
    def __mul__(self, other):
        """self * other"""
        raise NotImplementedError

    @abstractmethod
    def __rmul__(self, other):
        """other * self"""
        raise NotImplementedError

    @abstractmethod
    def __truediv__(self, other):
        """self / other: Should promote to float when necessary."""
        raise NotImplementedError

    @abstractmethod
    def __rtruediv__(self, other):
        """other / self"""
        raise NotImplementedError

    @abstractmethod
    def __pow__(self, exponent):
        """self ** exponent; should promote to float or complex when
        necessary."""
        raise NotImplementedError

    @abstractmethod
    def __rpow__(self, base):
        """base ** self"""
        raise NotImplementedError

    @abstractmethod
    def __abs__(self):
        """Returns the Real distance from 0. Called for abs(self)."""
        raise NotImplementedError

    @abstractmethod
    def conjugate(self):
        """(x+y*i).conjugate() returns (x-y*i)."""
        raise NotImplementedError

    @abstractmethod
    def __eq__(self, other):
        """self == other"""
        raise NotImplementedError


class Real(Complex):
    """To Complex, Real adds the operations that work on real numbers.

    In short, those are: a conversion to float, trunc(), divmod, %, <, <=,
    >, and >=.

    Real also provides defaults for the derived operations.
    """
    __slots__ = ()

    @abstractmethod
    def __float__(self):
        """Any Real can be converted to a native float object.

        Called for float(self)."""
        raise NotImplementedError

    @abstractmethod
    def __trunc__(self):
        """trunc(self): Truncates self to an Integral.

        Returns an Integral i such that:
          * i > 0 iff self > 0;
          * abs(i) <= abs(self);
          * for any Integral j satisfying the first two conditions,
            abs(i) >= abs(j) [i.e. i has "maximal" abs among those].
        i.e. "truncate towards 0".
        """
        raise NotImplementedError

    @abstractmethod
    def __floor__(self):
        """Finds the greatest Integral <= self."""
        raise NotImplementedError

    @abstractmethod
    def __ceil__(self):
        """Finds the least Integral >= self."""
        raise NotImplementedError

    @abstractmethod
    def __round__(self, ndigits=None):
        """Rounds self to ndigits decimal places, defaulting to 0.

        If ndigits is omitted or None, returns an Integral, otherwise
        returns a Real. Rounds half toward even.
        """
        raise NotImplementedError

    def __divmod__(self, other):
        """divmod(self, other): The pair (self // other, self % other).

        Sometimes this can be computed faster than the pair of operations.
        """
        return (self // other, self % other)

    def __rdivmod__(self, other):
        """divmod(other, self): The pair (other // self, other % self).

        Sometimes this can be computed faster than the pair of operations.
        """
        return (other // self, other % self)

    @abstractmethod
    def __floordiv__(self, other):
        """self // other: The floor() of self/other."""
        raise NotImplementedError

    @abstractmethod
    def __rfloordiv__(self, other):
        """other // self: The floor() of other/self."""
        raise NotImplementedError

    @abstractmethod
    def __mod__(self, other):
        """self % other"""
        raise NotImplementedError

    @abstractmethod
    def __rmod__(self, other):
        """other % self"""
        raise NotImplementedError

    @abstractmethod
    def __lt__(self, other):
        """self < other

        < on Reals defines a total ordering, except perhaps for NaN."""
        raise NotImplementedError

    @abstractmethod
    def __le__(self, other):
        """self <= other"""
        raise NotImplementedError

    # Concrete implementations of Complex abstract methods.
    def __complex__(self):
        """complex(self) == complex(float(self), 0)"""
        return complex(float(self))

    @property
    def real(self):
        """Real numbers are their real component."""
        return +self

    @property
    def imag(self):
        """Real numbers have no imaginary component."""
        return 0

    def conjugate(self):
        """Conjugate is a no-op for Reals."""
        return +self


class Rational(Real):
    """.numerator and .denominator should be in lowest terms."""
    __slots__ = ()

    @property
    @abstractmethod
    def numerator(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def denominator(self):
        raise NotImplementedError

    # Concrete implementation of Real's conversion to float.
    def __float__(self):
        """float(self) = self.numerator / self.denominator

        It's important that this conversion use the integer's "true"
        division rather than casting one side to float before dividing so
        that ratios of huge integers convert without overflowing.
        """
        return int(self.numerator) / int(self.denominator)


class Integral(Rational):
    """Integral adds methods that work on integral numbers.

    In short, these are conversion to int, pow with modulus, and the
    bit-string operations.
    """
    __slots__ = ()

    @abstractmethod
    def __int__(self):
        """int(self)"""
        raise NotImplementedError

    def __index__(self):
        """Called whenever an index is needed, such as in slicing"""
        return int(self)

    @abstractmethod
    def __pow__(self, exponent, modulus=None):
        """self ** exponent % modulus, but maybe faster.

        Accept the modulus argument if you want to support the 3-argument
        version of pow(). Raise a TypeError if exponent < 0 or any argument
        isn't Integral. Otherwise, just implement the 2-argument version
        described in Complex.
        """
        raise NotImplementedError

    @abstractmethod
    def __lshift__(self, other):
        """self << other"""
        raise NotImplementedError

    @abstractmethod
    def __rlshift__(self, other):
        """other << self"""
        raise NotImplementedError

    @abstractmethod
    def __rshift__(self, other):
        """self >> other"""
        raise NotImplementedError

    @abstractmethod
    def __rrshift__(self, other):
        """other >> self"""
        raise NotImplementedError

    @abstractmethod
    def __and__(self, other):
        """self & other"""
        raise NotImplementedError

    @abstractmethod
    def __rand__(self, other):
        """other & self"""
        raise NotImplementedError

    @abstractmethod
    def __xor__(self, other):
        """self ^ other"""
        raise NotImplementedError

    @abstractmethod
    def __rxor__(self, other):
        """other ^ self"""
        raise NotImplementedError

    @abstractmethod
    def __or__(self, other):
        """self | other"""
        raise NotImplementedError

    @abstractmethod
    def __ror__(self, other):
        """other | self"""
        raise NotImplementedError

    @abstractmethod
    def __invert__(self):
        """~self"""
        raise NotImplementedError

    # Concrete implementations of Rational and Real abstract methods.
    def __float__(self):
        """float(self) == float(int(self))"""
        return float(int(self))

    @property
    def numerator(self):
        """Integers are their own numerators."""
        return +self

    @property
    def denominator(self):
        """Integers have a denominator of 1."""
        return 1


# ── the builtin registrations ───────────────────────────────────────────────
#
# EVERY APPLICABLE LEVEL, not only the leaf -- see the module docstring for
# why one registration does not reach the classes above it here the way it
# does in CPython.

Complex.register(complex)
Number.register(complex)

Real.register(float)
Complex.register(float)
Number.register(float)

Integral.register(int)
Rational.register(int)
Real.register(int)
Complex.register(int)
Number.register(int)

# `bool` IS AN `int`, AND HAS TO SAY SO ITSELF HERE. CPython registers only
# `int` and `isinstance(True, Integral)` is True anyway, because `bool` is a
# real subclass of `int` and `_abc` walks its MRO. A builtin type reached as a
# VALUE in this frontend has no `__mro__` to walk -- `ABCMeta.__subclasscheck__`
# gets an empty tuple and falls through to comparing the registry entries by
# identity, where `bool is int` is False. So the subclass registers too, at
# every level, which is the same shape of workaround the docstring above
# describes for registering at each level rather than only the leaf.
Integral.register(bool)
Rational.register(bool)
Real.register(bool)
Complex.register(bool)
Number.register(bool)
