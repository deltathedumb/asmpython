# tier: spec
# ref: library/functions.html#min
# expect:
# 1 3
# 1 3
# a c
# (1, 2)
# [1, 3]
print(min(3, 1, 2), max(3, 1, 2))
print(min([3, 1, 2]), max([3, 1, 2]))
print(min("cab"), max("cab"))
print(min([(1, 9), (1, 2)]))
print(max([1, 2], [1, 3]))
