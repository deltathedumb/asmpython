# probe31: assert, pass, ellipsis, type hints
# assert
def check_positive(n: int) -> None:
    assert n > 0, "must be positive"

try:
    check_positive(5)
    print("ok")
except AssertionError:
    print("failed")

try:
    check_positive(-1)
    print("wrong")
except AssertionError:
    print("caught_assert")

# Optional type hints and None
def maybe(n: int) -> int | None:
    if n > 0:
        return n
    return None

v = maybe(5)
print(v)      # 5

# vars, __dict__ etc (likely unsupported)
# skip

# pass, ...
def noop() -> None:
    pass

noop()
print("noop_done")

# math operations
import math
print(math.floor(3.7))   # 3
print(math.ceil(3.2))    # 4
print(math.sqrt(16.0))   # 4.0
