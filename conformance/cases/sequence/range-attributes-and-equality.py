# tier: spec
# ref: library/stdtypes.html#range
# expect:
# 0 10 2
# 4 8
# 2 1
# True
# True
# [2, 4]
r = range(0, 10, 2)
print(r.start, r.stop, r.step)
print(r[2], r[-1])
print(r.index(4), r.count(4))
print(range(3) == range(3))
print(range(0, 3, 1) == range(3))
print(list(r[1:3]))
