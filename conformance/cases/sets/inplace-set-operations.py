# tier: spec
# ref: library/stdtypes.html#set
# expect:
# [2, 3]
# [2, 3, 9]
# [3, 9]
# [7, 9]
# [1, 3]
a = {1, 2, 3}
a &= {2, 3, 4}
print(sorted(a))
a |= {9}
print(sorted(a))
a -= {2}
print(sorted(a))
a ^= {3, 7}
print(sorted(a))
print(sorted({1, 2}.symmetric_difference({2, 3})))
