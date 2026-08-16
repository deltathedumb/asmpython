# expect:
# 55
# 10

from functools import reduce, lru_cache

@lru_cache(maxsize=128)
def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

print(fib(10))

total = reduce(lambda a, x: a + x, [1, 2, 3, 4], 0)
print(total)
