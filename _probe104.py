# probe104: complex typing patterns, Optional, Union-like checks
from typing import Optional

def find_first(lst: list, target: int) -> Optional[int]:
    for i, v in enumerate(lst):
        if v == target:
            return i
    return None

idx = find_first([3, 1, 4, 1, 5], 4)
print(idx)   # 2

idx2 = find_first([3, 1, 4, 1, 5], 99)
print(idx2)  # None
print(idx2 is None)   # True

# Optional with default
def get_val(d: dict, key: str, default: Optional[int] = None) -> Optional[int]:
    if key in d:
        return d[key]
    return default

d = {"a": 1, "b": 2}
print(get_val(d, "a"))       # 1
print(get_val(d, "z"))       # None
print(get_val(d, "z", 99))   # 99

# None checks
def safe_head(lst: list) -> Optional[int]:
    if not lst:
        return None
    return lst[0]

print(safe_head([1, 2, 3]))   # 1
print(safe_head([]))          # None

# Chained optional operations
def double_if_even(n: Optional[int]) -> Optional[int]:
    if n is None:
        return None
    if n % 2 == 0:
        return n * 2
    return None

print(double_if_even(4))    # 8
print(double_if_even(3))    # None
print(double_if_even(None)) # None
