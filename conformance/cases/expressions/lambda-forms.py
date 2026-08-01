# tier: spec
# ref: reference/expressions.html#lambda
# expect:
# (1, 2, (), [])
# (1, 3, (), [])
# (1, 3, (4,), ['k'])
# no-args
# [3, 2, 1]
f = lambda x, y=2, *a, **kw: (x, y, a, sorted(kw))
print(f(1))
print(f(1, 3))
print(f(1, 3, 4, k=5))
print((lambda: "no-args")())
print(sorted([3, 1, 2], key=lambda v: -v))
