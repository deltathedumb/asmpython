# expect:
# 3.33333
# division by zero
# 0.0
# 2.5

from typing import Optional

def safe_div(a: int, b: int) -> Optional[float]:
    if b == 0:
        return None
    return a / b

r = safe_div(10, 3)
if r is not None:
    print(r)

r2 = safe_div(5, 0)
if r2 is None:
    print("division by zero")

print(safe_div(0, 1))

def first_positive(a: float, b: float) -> Optional[float]:
    if a > 0.0:
        return a
    if b > 0.0:
        return b
    return None

v = first_positive(-1.0, 2.5)
if v is not None:
    print(v)
