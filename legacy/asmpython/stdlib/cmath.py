"""cmath module: mathematical functions for complex numbers.

In asmpython, complex numbers are represented as pairs of floats [real, imag].
"""
from __future__ import annotations
import math


pi: float = 3.141592653589793
e: float  = 2.718281828459045
tau: float = 6.283185307179586
inf: float = float("inf")
nan: float = float("nan")
infj: list[float] = [0.0, float("inf")]
nanj: list[float] = [0.0, float("nan")]


def phase(z: list[float]) -> float:
    """Return the phase (argument) of a complex number."""
    return math.atan2(z[1], z[0])


def polar(z: list[float]) -> list[float]:
    """Return the polar form (r, phi) of a complex number."""
    r: float = abs_c(z)
    phi: float = phase(z)
    return [r, phi]


def rect(r: float, phi: float) -> list[float]:
    """Return the complex number x with polar coordinates r and phi."""
    return [r * math.cos(phi), r * math.sin(phi)]


def abs_c(z: list[float]) -> float:
    """Return the modulus (absolute value) of a complex number."""
    return math.sqrt(z[0] * z[0] + z[1] * z[1])


def exp(z: list[float]) -> list[float]:
    """Return e**z."""
    er: float = math.exp(z[0])
    return [er * math.cos(z[1]), er * math.sin(z[1])]


def log(z: list[float], base: float = 0.0) -> list[float]:
    """Return the natural logarithm of z."""
    r: float = abs_c(z)
    phi: float = phase(z)
    result: list[float] = [math.log(r), phi]
    if base != 0.0:
        lb: float = math.log(base)
        result = [result[0] / lb, result[1] / lb]
    return result


def log10(z: list[float]) -> list[float]:
    """Return the base-10 logarithm of z."""
    return log(z, 10.0)


def sqrt(z: list[float]) -> list[float]:
    """Return the square root of z."""
    r: float = abs_c(z)
    sr: float = math.sqrt(r)
    phi: float = phase(z) / 2.0
    return [sr * math.cos(phi), sr * math.sin(phi)]


def sin(z: list[float]) -> list[float]:
    """Return sin(z)."""
    return [math.sin(z[0]) * math.cosh(z[1]),
            math.cos(z[0]) * math.sinh(z[1])]


def cos(z: list[float]) -> list[float]:
    """Return cos(z)."""
    return [math.cos(z[0]) * math.cosh(z[1]),
            -math.sin(z[0]) * math.sinh(z[1])]


def tan(z: list[float]) -> list[float]:
    """Return tan(z)."""
    s: list[float] = sin(z)
    c: list[float] = cos(z)
    d: float = c[0] * c[0] + c[1] * c[1]
    if d == 0.0:
        raise ZeroDivisionError("tan() of a number with cos=0")
    return [(s[0] * c[0] + s[1] * c[1]) / d,
            (s[1] * c[0] - s[0] * c[1]) / d]


def asin(z: list[float]) -> list[float]:
    """Return arcsin(z)."""
    iz: list[float] = [-z[1], z[0]]
    t: list[float] = sqrt([1.0 - z[0] * z[0] + z[1] * z[1],
                           -2.0 * z[0] * z[1]])
    t = [t[0] + iz[0], t[1] + iz[1]]
    l: list[float] = log(t)
    return [l[1], -l[0]]


def acos(z: list[float]) -> list[float]:
    """Return arccos(z)."""
    s: list[float] = asin(z)
    return [pi / 2.0 - s[0], -s[1]]


def atan(z: list[float]) -> list[float]:
    """Return arctan(z)."""
    iz: list[float] = [z[1], -z[0]]
    l1: list[float] = log([1.0 + iz[0], iz[1]])
    l2: list[float] = log([1.0 - iz[0], -iz[1]])
    return [(l1[1] - l2[1]) / 2.0, -(l1[0] - l2[0]) / 2.0]


def sinh(z: list[float]) -> list[float]:
    """Return sinh(z)."""
    return [math.sinh(z[0]) * math.cos(z[1]),
            math.cosh(z[0]) * math.sin(z[1])]


def cosh(z: list[float]) -> list[float]:
    """Return cosh(z)."""
    return [math.cosh(z[0]) * math.cos(z[1]),
            math.sinh(z[0]) * math.sin(z[1])]


def tanh(z: list[float]) -> list[float]:
    """Return tanh(z)."""
    s: list[float] = sinh(z)
    c: list[float] = cosh(z)
    d: float = c[0] * c[0] + c[1] * c[1]
    if d == 0.0:
        raise ZeroDivisionError("tanh() undefined here")
    return [(s[0] * c[0] + s[1] * c[1]) / d,
            (s[1] * c[0] - s[0] * c[1]) / d]


def asinh(z: list[float]) -> list[float]:
    """Return arcsinh(z)."""
    t: list[float] = sqrt([1.0 + z[0] * z[0] - z[1] * z[1],
                           2.0 * z[0] * z[1]])
    t = [t[0] + z[0], t[1] + z[1]]
    return log(t)


def acosh(z: list[float]) -> list[float]:
    """Return arccosh(z)."""
    t: list[float] = sqrt([z[0] * z[0] - z[1] * z[1] - 1.0,
                           2.0 * z[0] * z[1]])
    t = [t[0] + z[0], t[1] + z[1]]
    return log(t)


def atanh(z: list[float]) -> list[float]:
    """Return arctanh(z)."""
    l1: list[float] = log([1.0 + z[0], z[1]])
    l2: list[float] = log([1.0 - z[0], -z[1]])
    return [(l1[0] - l2[0]) / 2.0, (l1[1] - l2[1]) / 2.0]


def isfinite(z: list[float]) -> int:
    """Return True if both components are finite."""
    return 1 if (math.isfinite(z[0]) and math.isfinite(z[1])) else 0


def isinf(z: list[float]) -> int:
    """Return True if either component is infinite."""
    return 1 if (math.isinf(z[0]) or math.isinf(z[1])) else 0


def isnan(z: list[float]) -> int:
    """Return True if either component is NaN."""
    return 1 if (math.isnan(z[0]) or math.isnan(z[1])) else 0


def isclose(a: list[float], b: list[float], rel_tol: float = 1e-9,
            abs_tol: float = 0.0) -> int:
    """Return True if values are close."""
    diff_r: float = abs(a[0] - b[0])
    diff_i: float = abs(a[1] - b[1])
    if diff_r <= abs_tol and diff_i <= abs_tol:
        return 1
    scale_r: float = abs(b[0]) * rel_tol
    scale_i: float = abs(b[1]) * rel_tol
    return 1 if (diff_r <= scale_r and diff_i <= scale_i) else 0
