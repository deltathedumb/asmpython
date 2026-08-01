# tier: spec
# ref: library/__future__.html
# expect:
# int
# str
from __future__ import annotations

def f(a: int) -> bool:
    return True

print(f.__annotations__["a"])
print(type(f.__annotations__["a"]).__name__)
