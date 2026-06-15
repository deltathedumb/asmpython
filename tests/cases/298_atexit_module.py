# expect:
# 2
# b 3 4
# a 1 2
# 1
# 0

from __future__ import annotations
import atexit


def handler_a(x: int, y: int) -> int:
    print("a", x, y)
    return 0


def handler_b(x: int, y: int) -> int:
    print("b", x, y)
    return 0


atexit.register(handler_a, 1, 2)
atexit.register(handler_b, 3, 4)
print(atexit._ncallbacks())
# Handlers run in reverse registration order, like CPython's atexit.
atexit._run_exitfuncs()
atexit.unregister(handler_a)
print(atexit._ncallbacks())
atexit._clear()
print(atexit._ncallbacks())
