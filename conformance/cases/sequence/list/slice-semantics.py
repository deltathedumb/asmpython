# tier: spec
# ref: reference/expressions.html#slicings
# expect:
# [1, 2, 3]
# [0, 1, 2]
# [3, 4, 5]
# [0, 2, 4]
# [5, 4, 3, 2, 1, 0]
# [4, 5]
# []
xs = [0, 1, 2, 3, 4, 5]
print(xs[1:4])
print(xs[:3])
print(xs[3:])
print(xs[::2])
print(xs[::-1])
print(xs[-2:])
print(xs[10:20])
