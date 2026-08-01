# probes: functools.cache exists (3.9+)
# expect:
# 10
# 10
import functools


@functools.cache
def twice(n):
    return n * 2


print(twice(5))
print(twice(5))
