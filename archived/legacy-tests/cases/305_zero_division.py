# expect:
# division by zero
# division by zero
# division by zero
# division by zero
# division by zero
# division by zero
# done

from __future__ import annotations

try:
    a: int = 5
    b: int = 0
    print(a // b)
except ZeroDivisionError as e:
    print(e)

try:
    a2: int = 5
    b2: int = 0
    print(a2 % b2)
except ZeroDivisionError as e:
    print(e)

try:
    c: float = 5.0
    d: float = 0.0
    print(c / d)
except ZeroDivisionError as e:
    print(e)

try:
    c2: float = 5.0
    d2: float = 0.0
    print(c2 // d2)
except ZeroDivisionError as e:
    print(e)

try:
    c3: float = 5.0
    d3: float = 0.0
    print(c3 % d3)
except ZeroDivisionError as e:
    print(e)

try:
    print(divmod(7, 0))
except ZeroDivisionError as e:
    print(e)

print("done")
