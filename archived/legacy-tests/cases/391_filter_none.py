# expect:
# 3
# 1
# 2

xs: list[int] = [0, 1, 2, 0, 3]
ys = list(filter(None, xs))
print(len(ys))
print(ys[0])
print(ys[1])
