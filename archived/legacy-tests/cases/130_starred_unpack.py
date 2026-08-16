# expect:
# 1
# [2, 3, 4]
# [1, 2, 3]
# 4
# 1 [2, 3, 4] 5
# a
# ['b', 'c', 'd']
# 1
# [2]
# 1.5
# [2.5, 3.5, 4.5]
# 2.5
# 1.5 [2.5, 3.5] 4.5
# 1 2 [3, 4, 5]

# Starred assignment unpacking (PEP 3132): a single `*name` target collects
# the "leftover" elements into a new list, wherever it appears.

a, *rest = [1, 2, 3, 4]
print(a)
print(rest)

*init, last = [1, 2, 3, 4]
print(init)
print(last)

first, *mid, last2 = [1, 2, 3, 4, 5]
print(first, mid, last2)

xs = ["a", "b", "c", "d"]
x, *ys = xs
print(x)
print(ys)

nums = [1, 2]
n, *empty = nums
print(n)
print(empty)

fs = [1.5, 2.5, 3.5, 4.5]
f0, *frest = fs
print(f0)
print(frest)
print(frest[0])

f1, *fmid, f2 = fs
print(f1, fmid, f2)

a2, b2, *c2 = [1, 2, 3, 4, 5]
print(a2, b2, c2)
