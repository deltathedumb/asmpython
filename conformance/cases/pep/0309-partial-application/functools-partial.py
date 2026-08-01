# tier: spec
# ref: library/functools.html#functools.partial
# expect:
# 3 6
# 13
# True (1,)
import functools

def add(a, b, c=0):
    return a + b + c

p = functools.partial(add, 1)
print(p(2), p(2, c=3))
q = functools.partial(add, c=10)
print(q(1, 2))
print(p.func is add, p.args)
