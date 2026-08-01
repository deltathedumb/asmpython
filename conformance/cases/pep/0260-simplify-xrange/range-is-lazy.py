# tier: spec
# ref: library/stdtypes.html#range
# expect:
# 1000000000000
# 1000000
# True
# range
# [0, 1, 2]
r = range(10 ** 12)
print(len(r))
print(r[10 ** 6])
print(10 ** 11 in r)
print(type(r).__name__)
print(list(range(3)))
