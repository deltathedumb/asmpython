# expect:
# 1 a True
# 2 b False
# 3 c True

xs = [1, 2, 3]
ys = ["a", "b", "c"]
zs = [True, False, True]
for x, y, z in zip(xs, ys, zs):
    print(str(x) + " " + y + " " + str(z))
