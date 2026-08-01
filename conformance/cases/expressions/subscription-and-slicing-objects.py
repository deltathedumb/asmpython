# tier: spec
# ref: reference/expressions.html#subscriptions
# expect:
# 2 9
# [2, 3, 4] [0, 1, 2] [7, 8, 9]
# [0, 3, 6, 9] [9, 7, 5, 3, 1]
# [1, 2, 3]
# [0, 2, 4, 6, 8]
# 1 5 2 (1, 5, 2)
xs = list(range(10))
print(xs[2], xs[-1])
print(xs[2:5], xs[:3], xs[7:])
print(xs[::3], xs[::-2])
print(xs[slice(1, 4)])
print(xs[slice(None, None, 2)])
s = slice(1, 5, 2)
print(s.start, s.stop, s.step, s.indices(10))
