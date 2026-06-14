"""operator module: standard operators as functions.

Implements the most commonly used operator functions. itemgetter and
attrgetter return callables (implemented as simple lambda-like classes).
"""
from __future__ import annotations


def add(a: int, b: int) -> int:
    return a + b


def sub(a: int, b: int) -> int:
    return a - b


def mul(a: int, b: int) -> int:
    return a * b


def truediv(a: int, b: int) -> float:
    return float(a) / float(b)


def floordiv(a: int, b: int) -> int:
    return a // b


def mod(a: int, b: int) -> int:
    return a % b


def _pow(a: int, b: int) -> int:
    return a ** b


pow = _pow


def neg(a: int) -> int:
    return -a


def pos(a: int) -> int:
    return a


def _abs(a: int) -> int:
    if a < 0:
        return -a
    return a


abs = _abs


def eq(a: int, b: int) -> int:
    return 1 if a == b else 0


def ne(a: int, b: int) -> int:
    return 1 if a != b else 0


def lt(a: int, b: int) -> int:
    return 1 if a < b else 0


def le(a: int, b: int) -> int:
    return 1 if a <= b else 0


def gt(a: int, b: int) -> int:
    return 1 if a > b else 0


def ge(a: int, b: int) -> int:
    return 1 if a >= b else 0


def and_(a: int, b: int) -> int:
    return a & b


def or_(a: int, b: int) -> int:
    return a | b


def xor(a: int, b: int) -> int:
    return a ^ b


def not_(a: int) -> int:
    return 0 if a else 1


def lshift(a: int, b: int) -> int:
    return a << b


def rshift(a: int, b: int) -> int:
    return a >> b


def concat(a: str, b: str) -> str:
    return a + b


def contains(container: str, item: str) -> int:
    return 1 if item in container else 0


def getitem(obj: list, key: int) -> int:
    return obj[key]


def length_hint(obj: list) -> int:
    return len(obj)


def truth(a: int) -> int:
    return 1 if a else 0


class _ItemGetter:
    def __init__(self, key: int) -> None:
        self._key: int = key

    def __call__(self, obj: list) -> int:
        return obj[self._key]


def itemgetter(key: int) -> _ItemGetter:
    return _ItemGetter(key)
