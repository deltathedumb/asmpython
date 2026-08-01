# tier: spec
# ref: library/functools.html#functools.lru_cache
# expect:
# 2 2 4
# [1, 2]
# 1 2
# 2 3
import functools

calls = []

@functools.lru_cache(maxsize=2)
def slow(n):
    calls.append(n)
    return n * 2

print(slow(1), slow(1), slow(2))
print(calls)
print(slow.cache_info().hits, slow.cache_info().misses)
slow.cache_clear()
print(slow(1), len(calls))
