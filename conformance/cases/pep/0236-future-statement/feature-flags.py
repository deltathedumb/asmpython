# tier: spec
# ref: library/__future__.html
# expect:
# (3, 7)
# _Feature
# int
from __future__ import annotations
import __future__

print(__future__.annotations.optional[:2])
print(type(__future__.division).__name__)

def f(x: int) -> str:
    return "ok"

print(f.__annotations__["x"])
