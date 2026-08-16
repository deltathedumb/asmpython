# expect:
# 15
from functools import reduce
def add(a, b):
    return a + b
print(reduce(add, [1, 2, 3, 4, 5]))
# asmpython (beta/3.14.0) MISMATCH: prints '10196880\n' (wrong).
