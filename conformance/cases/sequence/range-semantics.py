# tier: spec
# ref: library/stdtypes.html#range
# expect:
# [0, 1, 2]
# [1, 2, 3]
# [0, 3, 6, 9]
# [3, 2, 1]
# []
# 1000000
# True
print(list(range(3)))
print(list(range(1, 4)))
print(list(range(0, 10, 3)))
print(list(range(3, 0, -1)))
print(list(range(0)))
print(len(range(10 ** 6)))
print(5 in range(10))
