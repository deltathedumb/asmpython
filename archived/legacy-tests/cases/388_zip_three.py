# expect:
# 1 x 10
# 2 y 20
# 3 z 30
# 0 a 0
# 1 b 1

a: list[int] = [1, 2, 3]
b: list[str] = ["x", "y", "z"]
c: list[int] = [10, 20, 30]
for x, y, z in zip(a, b, c):
    print(x, y, z)

# enumerate(zip(...)) with 2 iters still works
ns: list[int] = [0, 1, 2]
ss: list[str] = ["a", "b", "c"]
for i, (n, s) in enumerate(zip(ns, ss)):
    if i < 2:
        print(n, s, i)
