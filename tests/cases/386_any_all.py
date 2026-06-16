# expect:
# True
# False
# True
# False
# True
# False

xs: list[int] = [0, 0, 1]
print(any(xs))
print(all(xs))

ys: list[int] = [1, 2, 3]
print(any(ys))
print(any([0, 0, 0]))
print(all(ys))
print(all([1, 0, 1]))
