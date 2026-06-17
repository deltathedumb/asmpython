# expect:
# 3
# 1
# a
# 2
# b
# 3
# c
# 10
# x

xs = [1, 2, 3]
ys = ["a", "b", "c"]
pairs = list(zip(xs, ys))
print(len(pairs))
for i, s in pairs:
    print(i)
    print(s)

ns = [10, 20]
ss = ["x", "y"]
short = list(zip(ns, ss))
i, s = short[0]
print(i)
print(s)
