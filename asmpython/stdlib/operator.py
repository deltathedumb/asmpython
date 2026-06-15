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


def invert(a: int) -> int:
    return ~a


def inv(a: int) -> int:
    return ~a


def is_(a: int, b: int) -> int:
    return 1 if a is b else 0


def is_not(a: int, b: int) -> int:
    return 0 if a is b else 1


class _AttrGetter:
    def __init__(self, attr: str) -> None:
        self._attr: str = attr

    def __call__(self, obj: int) -> int:
        return getattr(obj, self._attr)


def attrgetter(attr: str) -> _AttrGetter:
    return _AttrGetter(attr)


class _MethodCaller:
    def __init__(self, name: str, arg: int = 0) -> None:
        self._name: str = name
        self._arg: int = arg
        self._has_arg: int = 0

    def set_arg(self, arg: int) -> None:
        self._arg = arg
        self._has_arg = 1

    def __call__(self, obj: int) -> int:
        m: int = getattr(obj, self._name)
        if self._has_arg:
            return m(self._arg)
        return m()


def methodcaller(name: str, arg: int = 0, has_arg: int = 0) -> _MethodCaller:
    mc: _MethodCaller = _MethodCaller(name)
    if has_arg:
        mc.set_arg(arg)
    return mc


def index(a: int) -> int:
    return a


def setitem(obj: list, key: int, value: int) -> None:
    obj[key] = value


def delitem(obj: list, key: int) -> None:
    obj.pop(key)


def ior(a: int, b: int) -> int:
    return a | b


def iand(a: int, b: int) -> int:
    return a & b


def ixor(a: int, b: int) -> int:
    return a ^ b


def iadd(a: int, b: int) -> int:
    return a + b


def isub(a: int, b: int) -> int:
    return a - b


def imul(a: int, b: int) -> int:
    return a * b


def ifloordiv(a: int, b: int) -> int:
    return a // b


def imod(a: int, b: int) -> int:
    return a % b


def ilshift(a: int, b: int) -> int:
    return a << b


def irshift(a: int, b: int) -> int:
    return a >> b
