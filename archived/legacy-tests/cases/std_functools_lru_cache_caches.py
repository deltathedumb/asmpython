# probes: lru_cache stops re-entering the function
# expect:
# 16
# 16
# 1
import functools

calls = []


@functools.lru_cache(maxsize=None)
def square(n):
    calls.append(n)
    return n * n


print(square(4))
print(square(4))
print(len(calls))
