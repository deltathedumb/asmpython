# expect:
# 6
# 1
# 4
# 5
# 10
# 20

xs: list[int] = [1, 2, 3]
ys: list[int] = [4, 5, 6]
zs = xs + ys
print(len(zs))
print(zs[0])
print(zs[3])
xs += [10, 20]
print(len(xs))
print(xs[3])
print(xs[4])
