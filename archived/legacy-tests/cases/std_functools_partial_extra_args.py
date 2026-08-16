# probes: partial prepends its bound arguments
# expect:
# x-y-z
import functools


def join3(a, b, c):
    return a + "-" + b + "-" + c


bound = functools.partial(join3, "x")
print(bound("y", "z"))
