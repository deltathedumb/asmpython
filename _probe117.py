# probe117: list[int] annotations, complex type hints
from typing import List, Dict, Tuple, Set, Optional

def process(nums: List[int]) -> List[int]:
    return [x * 2 for x in nums]

def lookup(d: Dict[str, int], key: str) -> Optional[int]:
    return d.get(key)

def first_two(t: Tuple[int, int, int]) -> Tuple[int, int]:
    return (t[0], t[1])

def unique(items: List[str]) -> Set[str]:
    return set(items)

result = process([1, 2, 3])
print(result)   # [2, 4, 6]

d = {"a": 1, "b": 2}
print(lookup(d, "a"))   # 1
print(lookup(d, "z"))   # None

t = (10, 20, 30)
pair = first_two(t)
print(pair[0])   # 10
print(pair[1])   # 20

s = unique(["hello", "world", "hello"])
print(len(s))   # 2

# Nested type hints
def flatten(matrix: List[List[int]]) -> List[int]:
    result: List[int] = []
    for row in matrix:
        for v in row:
            result.append(v)
    return result

m = [[1, 2, 3], [4, 5, 6]]
flat = flatten(m)
print(flat)   # [1, 2, 3, 4, 5, 6]
