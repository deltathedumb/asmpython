# probe91: functools-style patterns, memoization, cached_property
# Manual LRU-style cache using dict
_cache: dict = {}

def fib_cached(n: int) -> int:
    if n in _cache:
        return _cache[n]
    if n <= 1:
        result: int = n
    else:
        result = fib_cached(n - 1) + fib_cached(n - 2)
    _cache[n] = result
    return result

for i in range(10):
    print(fib_cached(i), end=" ")
print()   # 0 1 1 2 3 5 8 13 21 34

# Decorator that caches
def memoize(f):
    cache: dict = {}
    def wrapper(n: int) -> int:
        if n in cache:
            return cache[n]
        result: int = f(n)
        cache[n] = result
        return result
    return wrapper

@memoize
def triangle(n: int) -> int:
    if n <= 0:
        return 0
    return n + triangle(n - 1)

print(triangle(10))   # 55
print(triangle(5))    # 15

# Function as dict value
ops = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mul": lambda a, b: a * b,
}

print(ops["add"](3, 4))   # 7
print(ops["sub"](10, 3))  # 7
print(ops["mul"](3, 4))   # 12
