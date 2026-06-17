"""numbers module: numeric abstract base classes (ABC tower)."""
from __future__ import annotations


class Number:
    """Base class for all numbers."""

    def __hash__(self) -> int:
        return 0


class Complex(Number):
    """ABC for complex numbers."""

    def __add__(self, other: "Complex") -> "Complex":
        raise NotImplementedError

    def __radd__(self, other: "Complex") -> "Complex":
        raise NotImplementedError

    def __neg__(self) -> "Complex":
        raise NotImplementedError

    def __pos__(self) -> "Complex":
        raise NotImplementedError

    def __mul__(self, other: "Complex") -> "Complex":
        raise NotImplementedError

    def __rmul__(self, other: "Complex") -> "Complex":
        raise NotImplementedError

    def __truediv__(self, other: "Complex") -> "Complex":
        raise NotImplementedError

    def __rtruediv__(self, other: "Complex") -> "Complex":
        raise NotImplementedError

    def __abs__(self) -> float:
        raise NotImplementedError

    def conjugate(self) -> "Complex":
        raise NotImplementedError

    def __eq__(self, other: "Complex") -> int:
        raise NotImplementedError

    @property
    def real(self) -> float:
        raise NotImplementedError

    @property
    def imag(self) -> float:
        raise NotImplementedError


class Real(Complex):
    """ABC for real numbers."""

    def __trunc__(self) -> int:
        raise NotImplementedError

    def __floor__(self) -> int:
        raise NotImplementedError

    def __ceil__(self) -> int:
        raise NotImplementedError

    def __round__(self, ndigits: int = 0) -> "Real":
        raise NotImplementedError

    def __floordiv__(self, other: "Real") -> "Real":
        raise NotImplementedError

    def __rfloordiv__(self, other: "Real") -> "Real":
        raise NotImplementedError

    def __mod__(self, other: "Real") -> "Real":
        raise NotImplementedError

    def __rmod__(self, other: "Real") -> "Real":
        raise NotImplementedError

    def __lt__(self, other: "Real") -> int:
        raise NotImplementedError

    def __le__(self, other: "Real") -> int:
        raise NotImplementedError

    @property
    def real(self) -> float:
        return float(self)

    @property
    def imag(self) -> float:
        return 0.0

    def conjugate(self) -> "Real":
        return self


class Rational(Real):
    """ABC for rational numbers."""

    @property
    def numerator(self) -> int:
        raise NotImplementedError

    @property
    def denominator(self) -> int:
        raise NotImplementedError


class Integral(Rational):
    """ABC for integral numbers."""

    def __pow__(self, exponent: int, modulus: int = 0) -> "Integral":
        raise NotImplementedError

    def __lshift__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __rlshift__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __rshift__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __rrshift__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __and__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __rand__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __xor__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __rxor__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __or__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __ror__(self, other: "Integral") -> "Integral":
        raise NotImplementedError

    def __invert__(self) -> "Integral":
        raise NotImplementedError

    def __int__(self) -> int:
        raise NotImplementedError

    @property
    def numerator(self) -> int:
        return int(self)

    @property
    def denominator(self) -> int:
        return 1

    @property
    def real(self) -> float:
        return float(int(self))

    @property
    def imag(self) -> float:
        return 0.0

    def conjugate(self) -> "Integral":
        return self

    def __floor__(self) -> int:
        return int(self)

    def __ceil__(self) -> int:
        return int(self)

    def __round__(self, ndigits: int = 0) -> "Integral":
        return self
