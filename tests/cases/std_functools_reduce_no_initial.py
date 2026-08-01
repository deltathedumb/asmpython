# probes: reduce works without an initial value
# expect:
# 10
import functools

print(functools.reduce(lambda a, b: a + b, [1, 2, 3, 4]))
